"""指标采集：Token / TPS / LLM 耗时 / 上下文占用 / 缓存命中率。

设计权衡
--------
1. 采集**只依赖回调**：`core/llm.py` 每次调用后回调 `on_usage(payload)`，
   本模块把它累加进计数器。这样 metrics 与 LLM 实现解耦（真实客户端与离线 mock 同构），
   也不需要在编排层到处埋计时点。
2. `snapshot()` 的字段**逐一对应**契约里 `metric` 事件的字段，顺序也保持一致，
   便于前端直接渲染；额外字段（`last_purpose`、`llm_errors`）不影响契约。
3. `context_tokens` 取"最近一次调用的 prompt_tokens"——它才是**当前上下文占用**；
   "累计 prompt_tokens"是花费而不是占用，两者混用会让占用率虚高。
4. 缓存命中率 = cached_tokens / prompt_tokens（命中率按"输入侧"定义，无输入时为 0）。
5. 未安装/未绑定回调时（例如 mock 客户端不回调）本模块仍可正常 `snapshot()`，
   只是总量为 0——不会抛异常打断编排。

事件订阅：`add_listener(fn)` 注册的监听器会在每次 usage 后收到一份快照，
供图节点把它转成 `metric` 事件实时下发（契约要求"每次 LLM 调用后推送"）。
"""
from __future__ import annotations

import logging
import threading
from typing import Any, Callable

logger = logging.getLogger(__name__)

# 默认上下文窗口（模型未申报时的兜底；仅用于计算 context_ratio）
DEFAULT_CONTEXT_WINDOW = 65536

# metric 事件字段全集（顺序即前端渲染顺序）
METRIC_FIELDS = ("tps", "llm_ms", "prompt_tokens", "completion_tokens", "total_tokens",
                 "cached_tokens", "cache_hit_rate", "context_tokens", "context_window",
                 "context_ratio", "llm_calls", "tool_calls")


def _as_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _as_float(value: Any) -> float:
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0


class MetricsCollector:
    """累计 LLM 用量并产出契约所需的指标快照。线程安全（回调可能来自工作线程）。"""

    def __init__(self, context_window: int = 0):
        self._lock = threading.RLock()
        self._window = _as_int(context_window) or DEFAULT_CONTEXT_WINDOW
        self.prompt_tokens = 0
        self.completion_tokens = 0
        self.total_tokens = 0
        self.cached_tokens = 0
        self.llm_ms = 0.0
        self.llm_calls = 0
        self.tool_calls = 0
        self.tool_retries = 0
        self.llm_errors = 0
        self.context_tokens = 0          # 最近一次调用的输入 token（上下文占用）
        self.last_purpose = ""
        self.last_model = ""
        self._listeners: list[Callable[[dict], None]] = []
        self._bound: list[tuple[Any, Callable | None]] = []   # (llm, 原回调) 便于还原

    # ---------------- 订阅 ----------------
    def add_listener(self, fn: Callable[[dict], None]) -> Callable[[], None]:
        """注册"每次 usage 后"的快照监听器，返回反注册函数。"""
        if fn not in self._listeners:
            self._listeners.append(fn)

        def _off() -> None:
            if fn in self._listeners:
                self._listeners.remove(fn)

        return _off

    def _notify(self) -> None:
        snapshot = self.snapshot()
        for fn in list(self._listeners):
            try:
                fn(snapshot)
            except Exception:  # 监听器异常不影响采集主链路
                logger.debug("metric 监听器异常", exc_info=True)

    # ---------------- 采集 ----------------
    def on_usage(self, payload: dict | None = None) -> dict:
        """LLM 用量回调入口（`llm.on_usage = collector.on_usage`）。返回最新快照。"""
        data = dict(payload or {})
        with self._lock:
            prompt = _as_int(data.get("prompt_tokens"))
            completion = _as_int(data.get("completion_tokens"))
            total = _as_int(data.get("total_tokens")) or (prompt + completion)
            self.prompt_tokens += prompt
            self.completion_tokens += completion
            self.total_tokens += total
            self.cached_tokens += _as_int(data.get("cached_tokens"))
            latency = _as_float(data.get("latency_ms"))
            if not latency:
                tps = _as_float(data.get("tps"))
                latency = (completion / tps * 1000.0) if tps > 0 else 0.0
            self.llm_ms += latency
            self.llm_calls += 1
            self.context_tokens = prompt or self.context_tokens
            self.last_purpose = str(data.get("purpose") or "")
            self.last_model = str(data.get("model") or "")
        self._notify()
        return self.snapshot()

    def record_tool_call(self, retries: int = 0) -> None:
        """工具调用计数（由事件桥接在 tool_result/tool_error 时调用）。"""
        with self._lock:
            self.tool_calls += 1
            self.tool_retries += max(0, _as_int(retries))

    def record_error(self) -> None:
        with self._lock:
            self.llm_errors += 1

    def reset(self) -> None:
        with self._lock:
            self.prompt_tokens = self.completion_tokens = self.total_tokens = 0
            self.cached_tokens = 0
            self.llm_ms = 0.0
            self.llm_calls = self.tool_calls = self.tool_retries = self.llm_errors = 0
            self.context_tokens = 0
            self.last_purpose = self.last_model = ""

    # ---------------- 绑定 LLM ----------------
    def bind_llm(self, llm: Any) -> bool:
        """把 `llm.on_usage` 接到本收集器；已有回调会被**链式保留**（不覆盖他人逻辑）。

        返回 True 表示绑定成功。客户端不支持该属性（例如只实现 chat 的精简 mock）时
        返回 False，由调用方决定是否接受"指标为 0"。
        """
        if llm is None:
            return False
        previous = getattr(llm, "on_usage", None)
        if previous is not None and not callable(previous):
            previous = None

        def _hook(payload: dict | None = None) -> None:
            if callable(previous):
                try:
                    previous(payload)
                except Exception:
                    logger.debug("原有 on_usage 回调异常", exc_info=True)
            self.on_usage(payload)

        try:
            setattr(llm, "on_usage", _hook)
        except Exception as e:  # 只读属性/冻结实例等
            logger.debug("无法绑定 on_usage（%s），指标将由消费端手工累计", e)
            return False
        self._bound.append((llm, previous))
        return True

    def unbind_llm(self, llm: Any = None) -> None:
        """解绑（还原原回调），避免跨 run 重复累加。"""
        for item in list(self._bound):
            target, previous = item
            if llm is not None and target is not llm:
                continue
            try:
                if previous is None:
                    try:
                        delattr(target, "on_usage")
                    except AttributeError:
                        setattr(target, "on_usage", None)
                else:
                    setattr(target, "on_usage", previous)
            except Exception:
                logger.debug("解绑 on_usage 失败", exc_info=True)
            self._bound.remove(item)

    # ---------------- 输出 ----------------
    @property
    def tps(self) -> float:
        """整体吞吐：completion_tokens / 总 LLM 秒数（无数据时为 0）。"""
        with self._lock:
            if self.llm_ms <= 0:
                return 0.0
            return round(self.completion_tokens / (self.llm_ms / 1000.0), 2)

    def snapshot(self) -> dict:
        """契约 `metric` 事件载荷（字段与顺序逐一对应）。"""
        with self._lock:
            window = self._window or DEFAULT_CONTEXT_WINDOW
            prompt = self.prompt_tokens
            return {
                "tps": self.tps,
                "llm_ms": round(self.llm_ms, 1),
                "prompt_tokens": prompt,
                "completion_tokens": self.completion_tokens,
                "total_tokens": self.total_tokens,
                "cached_tokens": self.cached_tokens,
                "cache_hit_rate": round(self.cached_tokens / prompt, 4) if prompt else 0.0,
                "context_tokens": self.context_tokens,
                "context_window": window,
                "context_ratio": round(self.context_tokens / window, 4) if window else 0.0,
                "llm_calls": self.llm_calls,
                "tool_calls": self.tool_calls,
                # —— 契约之外的补充字段（前端可忽略） ——
                "tool_retries": self.tool_retries,
                "llm_errors": self.llm_errors,
                "last_purpose": self.last_purpose,
                "last_model": self.last_model,
            }

    def merge_usage_stats(self, stats: dict | None) -> None:
        """兜底：把 `llm.usage_stats()` 的结果并入（on_usage 未被回调时使用）。

        取**较大值**而非相加，避免与 on_usage 累加结果重复计数。
        """
        data = dict(stats or {})
        if not data:
            return
        with self._lock:
            for key, attr in (("prompt_tokens", "prompt_tokens"),
                              ("completion_tokens", "completion_tokens"),
                              ("total_tokens", "total_tokens"),
                              ("cached_tokens", "cached_tokens"),
                              ("llm_calls", "llm_calls")):
                if key in data:
                    setattr(self, attr, max(getattr(self, attr), _as_int(data.get(key))))
            if data.get("latency_ms"):
                self.llm_ms = max(self.llm_ms, _as_float(data.get("latency_ms")))
            if data.get("context_tokens"):
                self.context_tokens = max(self.context_tokens, _as_int(data.get("context_tokens")))
        self._notify()


def bind_llm(llm: Any, collector: MetricsCollector | None = None) -> MetricsCollector:
    """便捷函数：`bind_llm(llm)` → 新建（或复用）收集器并绑定。

    离线 mock 客户端同样支持（`MockLLM.chat` 后由 llm.py 侧回调）。
    """
    col = collector or MetricsCollector(_window_from_cfg())
    col.bind_llm(llm)
    return col


def _window_from_cfg() -> int:
    """从配置读取上下文窗口（未配置则用默认值）。"""
    try:
        from config import load_config
        return _as_int(getattr(load_config(), "context_window", 0)) or DEFAULT_CONTEXT_WINDOW
    except Exception:
        return DEFAULT_CONTEXT_WINDOW


__all__ = ["MetricsCollector", "bind_llm", "METRIC_FIELDS", "DEFAULT_CONTEXT_WINDOW"]
