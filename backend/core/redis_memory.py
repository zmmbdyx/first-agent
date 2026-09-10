"""短期记忆：Redis（带 TTL）优先，不可用时降级为进程内 dict。

设计权衡
--------
1. **降级不是例外而是常态**：契约要求「Redis 连接失败自动降级，不得抛异常阻断启动」。
   任何 Redis 异常（连不上、协议不兼容、命令报错）都会被吞掉并切到进程内实现，
   同时把原因记录在 `degraded_reason` 里，供 `/api/health` 解释"为什么是内存后端"。
2. `from_url(..., protocol=2)`：部分自建/代理部署只支持 RESP2，新版客户端默认发 RESP3 的
   `HELLO` 命令会直接报错。这里主动用 RESP2 握手，兼容性最好。
3. 进程内实现同样支持"条数窗口"（`_MAX_ITEMS`），避免长会话把内存撑爆；
   Redis 侧用 ZSET 按时间戳打分，既能取最近 N 条也能按 TTL 过期。
4. 本模块**不碰**业务会话记忆（`core/memory.py` 负责落盘）；它只保存"最近若干条
   消息/事件"这种可丢弃的短期上下文。
"""
from __future__ import annotations

import json
import logging
import threading
import time
from typing import Any

from config import Settings

logger = logging.getLogger(__name__)

_KEY_PREFIX = "pathforge:stm:"
_DEFAULT_MAX_ITEMS = 200
_DEFAULT_LIMIT = 20


class ShortTermMemory:
    """最近若干条消息的滑动窗口存储。

    用法::

        stm = ShortTermMemory(cfg)
        stm.append(session_id, {"role": "user", "content": "..."})
        stm.get(session_id, limit=10)
    """

    def __init__(self, cfg: Settings, ttl: int = 86400, max_items: int = _DEFAULT_MAX_ITEMS,
                 redis_url: str | None = None):
        self.cfg = cfg
        self.ttl = max(1, int(ttl))
        self.max_items = max(1, int(max_items))
        self._lock = threading.RLock()
        self._mem: dict[str, list[dict]] = {}
        self.degraded_reason = ""
        self._redis: Any = None
        self._backend = "memory"
        self._connect(redis_url if redis_url is not None else getattr(cfg, "redis_url", ""))

    # ---------------- 连接 ----------------
    def _connect(self, url: str) -> None:
        url = (url or "").strip()
        if not url:
            self.degraded_reason = "未配置 REDIS_URL，使用进程内短期记忆"
            return
        try:
            import redis  # 延迟导入：未安装 redis 包也必须能跑
            client = redis.Redis.from_url(url, socket_connect_timeout=2, socket_timeout=2,
                                          decode_responses=True, protocol=2)
            client.ping()
        except Exception as e:  # 连接/协议/鉴权任何失败都降级
            self.degraded_reason = f"Redis 不可用（{type(e).__name__}: {e}），已降级为进程内短期记忆"
            logger.info(self.degraded_reason)
            self._redis = None
            self._backend = "memory"
            return
        self._redis = client
        self._backend = "redis"

    # ---------------- 内部工具 ----------------
    def _key(self, session_id: str) -> str:
        return f"{_KEY_PREFIX}{session_id or 'default'}"

    @staticmethod
    def _encode(item: Any) -> str:
        if isinstance(item, str):
            payload = {"content": item, "ts": time.time()}
        else:
            payload = dict(item or {})
            payload.setdefault("ts", time.time())
        try:
            return json.dumps(payload, ensure_ascii=False, default=str)
        except (TypeError, ValueError):
            return json.dumps({"content": str(item), "ts": time.time()}, ensure_ascii=False)

    @staticmethod
    def _decode(raw: Any) -> dict:
        try:
            data = json.loads(raw)
            return data if isinstance(data, dict) else {"content": str(data)}
        except (TypeError, ValueError):
            return {"content": str(raw)}

    # ---------------- 对外接口 ----------------
    def append(self, session_id: str, item: Any) -> dict:
        """追加一条短期记忆（dict 或字符串），返回落库条目。"""
        payload = json.loads(self._encode(item))
        if self._redis is not None:
            try:
                key = self._key(session_id)
                score = float(payload.get("ts") or time.time())
                self._redis.zadd(key, {json.dumps(payload, ensure_ascii=False, default=str): score})
                self._redis.zremrangebyrank(key, 0, -(self.max_items + 1))  # 保留最近 max_items 条
                self._redis.expire(key, self.ttl)
                return payload
            except Exception as e:
                self._degrade(e)
        with self._lock:
            bucket = self._mem.setdefault(session_id or "default", [])
            bucket.append(payload)
            if len(bucket) > self.max_items:
                del bucket[: len(bucket) - self.max_items]
        return payload

    def get(self, session_id: str, limit: int = _DEFAULT_LIMIT) -> list[dict]:
        """取最近 `limit` 条（时间升序，最早在前）。"""
        size = max(1, int(limit or _DEFAULT_LIMIT))
        if self._redis is not None:
            try:
                rows = self._redis.zrange(self._key(session_id), -size, -1)
                return [self._decode(r) for r in (rows or [])]
            except Exception as e:
                self._degrade(e)
        with self._lock:
            return list(self._mem.get(session_id or "default", []))[-size:]

    def clear(self, session_id: str) -> bool:
        """清空某会话的短期记忆。"""
        ok = True
        if self._redis is not None:
            try:
                self._redis.delete(self._key(session_id))
            except Exception as e:
                self._degrade(e)
                ok = False
        with self._lock:
            self._mem.pop(session_id or "default", None)
        return ok

    @property
    def backend_name(self) -> str:
        return "redis" if self._redis is not None else "memory"

    def _degrade(self, exc: Exception) -> None:
        """运行期 Redis 出错 → 永久降级到进程内，避免每次调用都等超时。"""
        self.degraded_reason = f"Redis 运行期异常（{type(exc).__name__}: {exc}），已降级为进程内短期记忆"
        logger.warning(self.degraded_reason)
        self._redis = None
        self._backend = "memory"

    def stats(self) -> dict:
        with self._lock:
            sessions = len(self._mem)
            items = sum(len(v) for v in self._mem.values())
        return {"backend": self.backend_name, "sessions": sessions, "items": items,
                "ttl": self.ttl, "max_items": self.max_items,
                "degraded_reason": self.degraded_reason}


__all__ = ["ShortTermMemory"]
