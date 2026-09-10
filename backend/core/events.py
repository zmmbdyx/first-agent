"""事件名常量 + `EventBridge`：把既有 `agent.bus` 的同步事件转成契约事件 dict。

设计权衡
--------
1. **既有总线是同步回调，且可能来自工作线程**（工具重试里带 `time.sleep` 的重试就在
   工作线程里发事件）。因此桥接不直接 await、不直接 yield，只做两件事：
   ① 用 `queue.Queue` 入队（线程安全）；② 用 `loop.call_soon_threadsafe` 置位
   "有新事件/收到 ask_user" 的 asyncio.Event，让事件循环侧的消费协程立刻醒来。
2. **字段补齐在桥接层完成**，业务代码不必知道 SSE 契约：`tool_call` 生成 `call_id`，
   `tool_result/tool_error` 按 `call_id` 回填 `elapsed_ms` 与 `ok`；
   `node_start/node_end` 补 `node_id/label/seq` 并记录节点耗时。
3. 桥接同时是**轨迹喂数口**：每个事件都会转交 `TrajectoryRecorder`，
   图节点无需自己拼轨迹。
4. `drain()` 只做非阻塞搬运（无 sleep）：事件在 `_run_task` 返回前就已入队，
   搬运与节点结束的顺序天然正确。
"""
from __future__ import annotations

import asyncio
import logging
import queue
import threading
import time
import uuid
from typing import Any, Callable

logger = logging.getLogger(__name__)

# ---------------- 事件名常量（与契约 2.2 表格逐一对应） ----------------
RUN_STARTED = "run_started"
SESSION_INFO = "session_info"
QUEUE_POSITION = "queue_position"
USER_MESSAGE = "user_message"
NODE_START = "node_start"
NODE_END = "node_end"
PLAN_CREATED = "plan_created"
TASK_START = "task_start"
THOUGHT = "thought"
TOOL_CALL = "tool_call"
TOOL_RESULT = "tool_result"
TOOL_ERROR = "tool_error"
RETRY = "retry"
TASK_FINISH = "task_finish"
ASK_USER = "ask_user"
SECURITY_BLOCK = "security_block"
FACTS_UPDATED = "facts_updated"
METRIC = "metric"
TRAJECTORY = "trajectory"
FINAL_ANSWER = "final_answer"
RUN_DONE = "run_done"
INTERRUPTED = "interrupted"
ROLLED_BACK = "rolled_back"
ERROR = "error"
HEARTBEAT = "heartbeat"

# 桥接额外识别但不外发的内部事件（既有 agent 会发射，前端无对应契约）
INTERNAL_EVENTS = ("plan_fallback", "resumed")

# 已经带完整契约字段、可直接透传的事件
PASSTHROUGH = frozenset({
    RUN_STARTED, SESSION_INFO, QUEUE_POSITION, USER_MESSAGE, PLAN_CREATED, TASK_START,
    THOUGHT, RETRY, TASK_FINISH, ASK_USER, SECURITY_BLOCK, FACTS_UPDATED, METRIC,
    TRAJECTORY, FINAL_ANSWER, RUN_DONE, INTERRUPTED, ROLLED_BACK, ERROR, HEARTBEAT,
})


def new_call_id() -> str:
    """工具调用 id（契约要求 `tool_call`/`tool_result`/`tool_error` 三处一致）。"""
    return "c" + uuid.uuid4().hex[:12]


class EventBridge:
    """订阅既有事件总线，补齐契约字段并转交轨迹记录器。

    用法::

        bridge = EventBridge(agent, loop=asyncio.get_running_loop(), recorder=rec)
        unsubscribe = bridge.subscribe()
        ...
        async for step in graph.astream(...):
            for evt in bridge.drain():
                yield evt
        unsubscribe()
    """

    def __init__(self, agent: Any, loop: asyncio.AbstractEventLoop | None = None,
                 recorder: Any = None, metrics: Any = None,
                 max_queue: int = 10000) -> None:
        self.agent = agent
        self.recorder = recorder
        self.metrics = metrics
        self._loop = loop
        self._lock = threading.RLock()
        self._queue: "queue.Queue[dict]" = queue.Queue(maxsize=max_queue)
        self._history: list[dict] = []
        self._calls: dict[tuple[str, str], list[dict]] = {}   # (task_id, tool) -> 调用栈
        self._calls_by_id: dict[str, dict] = {}
        self._unsubscribe: Callable[[], Any] | None = None
        self._wake = asyncio.Event()
        self._ask = asyncio.Event()
        self.last_seq = 0
        self.count = 0

    # ---------------- 订阅生命周期 ----------------
    def subscribe(self) -> Callable[[], Any]:
        """挂到既有总线上，返回反注册函数（幂等）。"""
        if self._unsubscribe is not None:
            return self._unsubscribe
        bus = getattr(self.agent, "bus", None)
        if bus is None:
            return lambda: None
        self._unsubscribe = bus.subscribe(self._on_event)
        return self._unsubscribe

    def unsubscribe(self) -> None:
        with self._lock:
            fn, self._unsubscribe = self._unsubscribe, None
        if fn is not None:
            try:
                fn()
            except Exception:  # 已反注册/总线被替换
                logger.debug("事件桥接反注册失败", exc_info=True)

    # ---------------- 同步入口（可能在任意线程被调用） ----------------
    def _on_event(self, evt: dict) -> None:
        try:
            normalized = self.normalize(evt)
        except Exception as e:  # 归一化失败绝不能影响业务线程
            logger.debug("事件归一化失败：%s", e)
            normalized = {"type": str((evt or {}).get("type") or ERROR),
                          "message": f"事件归一化失败: {e}"}
        try:
            self._queue.put_nowait(normalized)
        except queue.Full:  # 消费端堵塞：丢最旧，保最新（前端看最新状态更有意义）
            try:
                self._queue.get_nowait()
                self._queue.put_nowait(normalized)
            except queue.Empty:
                pass
        with self._lock:
            self._history.append(normalized)
            if len(self._history) > 2000:
                del self._history[:500]
        self._dispatch(normalized)
        self._signal(wake=True, ask=normalized.get("type") == ASK_USER)

    def _signal(self, wake: bool = False, ask: bool = False) -> None:
        """唤醒事件循环侧的消费者（线程安全）。

        `asyncio.Event.set()` 本身是线程安全的，因此即使事件循环尚未绑定
        （构造与 `attach_loop` 之间的窗口期，或单元测试直连）也能安全置位；
        绑定后额外用 `call_soon_threadsafe` 提醒循环立刻调度等待协程。
        """
        try:
            if wake:
                self._wake.set()
            if ask:
                self._ask.set()
        except Exception:  # 事件对象已被替换等极端情况
            logger.debug("唤醒信号置位失败", exc_info=True)
        loop = self._loop
        if loop is None or loop.is_closed():
            return
        try:
            loop.call_soon_threadsafe(lambda: None)   # 提醒循环尽快处理就绪的等待者
        except RuntimeError:
            pass

    # ---------------- 归一化 ----------------
    def normalize(self, evt: dict) -> dict:
        """补齐契约字段；未知事件类型原样透传（附带 type）。"""
        data = dict(evt or {})
        etype = str(data.pop("type", "") or ERROR)
        out: dict[str, Any] = {"type": etype}
        out.update(data)
        if etype in (TOOL_CALL, TOOL_RESULT, TOOL_ERROR):
            out = self._normalize_tool(etype, out)
        elif etype == NODE_START:
            out.setdefault("seq", self._next_seq())
            out.setdefault("node_id", f"n{out['seq']}")
            out.setdefault("label", out.get("node") or "")
        elif etype == NODE_END:
            out.setdefault("status", "done")
            out.setdefault("error", "")
        with self._lock:
            self.count += 1
        return out

    def _next_seq(self) -> int:
        with self._lock:
            self.last_seq += 1
            return self.last_seq

    def _normalize_tool(self, etype: str, out: dict) -> dict:
        """工具事件的 `call_id` 配对与 `elapsed_ms`/`ok` 计算。"""
        task_id = str(out.get("task_id") or "")
        tool = str(out.get("tool") or "")
        key = (task_id, tool)
        now = time.monotonic()
        if etype == TOOL_CALL:
            call_id = str(out.get("call_id") or new_call_id())
            record = {"call_id": call_id, "t0": now, "task_id": task_id, "tool": tool,
                      "args": out.get("args") or {}, "attempts": 0}
            with self._lock:
                self._calls.setdefault(key, []).append(record)
                self._calls_by_id[call_id] = record
            out["call_id"] = call_id
            out.setdefault("cost", "")
            out.setdefault("args", {})
            return out

        if etype == RETRY:
            with self._lock:
                stack = self._calls.get(key) or []
                if stack:
                    stack[-1]["attempts"] = max(stack[-1].get("attempts", 0), int(out.get("attempt") or 0))
            if self.metrics is not None:
                self.metrics.tool_retries += 1
            return out

        call_id = str(out.get("call_id") or "")
        record: dict | None = None
        with self._lock:
            stack = self._calls.get(key) or []
            if call_id and call_id in self._calls_by_id:
                record = self._calls_by_id.get(call_id)
                if record in stack:
                    stack.remove(record)
            elif stack:
                record = stack.pop()
                call_id = str(record.get("call_id") or "")
        if record is None:
            record = {"call_id": call_id or new_call_id(), "t0": now, "tool": tool,
                      "task_id": task_id, "attempts": 0}
        out["call_id"] = str(record.get("call_id") or call_id or new_call_id())
        out.setdefault("elapsed_ms", int(max(0.0, now - float(record.get("t0") or now)) * 1000))
        out["attempts"] = int(record.get("attempts") or 0)
        if etype == TOOL_RESULT:
            out["ok"] = True
        else:
            out["ok"] = False
        return out

    # ---------------- 轨迹喂数 ----------------
    def _dispatch(self, evt: dict) -> None:
        rec = self.recorder
        if rec is None:
            return
        etype = evt.get("type")
        try:
            if etype == TASK_START:
                rec.set_task(evt.get("task_id"), evt.get("title") or "", evt.get("tool") or "")
            elif etype == THOUGHT:
                rec.record_thought(evt.get("task_id"), evt.get("step"), evt.get("thought") or "")
            elif etype == TOOL_CALL:
                rec.record_tool_call(evt)
            elif etype in (TOOL_RESULT, TOOL_ERROR):
                rec.record_tool_result(evt)
            elif etype == ASK_USER:
                rec.record_thought(evt.get("task_id"), None,
                                   f"需要用户补充信息：{evt.get('question') or ''}")
            elif etype == NODE_END:
                rec.record_node_metric(evt)
        except Exception:  # 轨迹记录异常不得影响业务线程
            logger.debug("轨迹记录失败（%s）", etype, exc_info=True)

    # ---------------- 消费侧 ----------------
    def drain(self, limit: int = 1000) -> list[dict]:
        """非阻塞取出全部待发事件（保持发生顺序）。"""
        out: list[dict] = []
        while len(out) < limit:
            try:
                out.append(self._queue.get_nowait())
            except queue.Empty:
                break
        return out

    async def wait(self, timeout: float = 0.05) -> None:
        """等待"有新事件"（图节点执行期间由工作线程唤醒；超时兜底防挂死）。"""
        try:
            await asyncio.wait_for(self._wake.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            pass
        finally:
            self._wake.clear()

    def ask_requested(self) -> bool:
        """是否收到过 `ask_user`（用于判定本节点需要图中断）。"""
        return self._ask.is_set()

    def clear_ask(self) -> None:
        self._ask.clear()

    async def wait_ask(self, timeout: float = 0.01) -> bool:
        """短暂等待 `ask_user` 到达（工具事件与 ask_user 事件同批入队，无需长等）。"""
        if self._ask.is_set():
            return True
        try:
            await asyncio.wait_for(self._ask.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            return self._ask.is_set()
        return True

    def history(self, types: tuple[str, ...] | None = None) -> list[dict]:
        """已发生的全部事件（调试/测试用；生产 SSE 走 `drain`）。"""
        with self._lock:
            rows = list(self._history)
        return [e for e in rows if not types or e.get("type") in types]

    def last(self, etype: str) -> dict | None:
        with self._lock:
            for evt in reversed(self._history):
                if evt.get("type") == etype:
                    return evt
        return None

    def counts(self) -> dict:
        with self._lock:
            rows = list(self._history)
        out: dict[str, int] = {}
        for evt in rows:
            key = str(evt.get("type"))
            out[key] = out.get(key, 0) + 1
        return out

    @property
    def loop(self) -> asyncio.AbstractEventLoop | None:
        return self._loop

    def attach_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop


__all__ = [
    "EventBridge", "new_call_id",
    "RUN_STARTED", "SESSION_INFO", "QUEUE_POSITION", "USER_MESSAGE", "NODE_START", "NODE_END",
    "PLAN_CREATED", "TASK_START", "THOUGHT", "TOOL_CALL", "TOOL_RESULT", "TOOL_ERROR", "RETRY",
    "TASK_FINISH", "ASK_USER", "SECURITY_BLOCK", "FACTS_UPDATED", "METRIC", "TRAJECTORY",
    "FINAL_ANSWER", "RUN_DONE", "INTERRUPTED", "ROLLED_BACK", "ERROR", "HEARTBEAT",
    "PASSTHROUGH", "INTERNAL_EVENTS",
]
