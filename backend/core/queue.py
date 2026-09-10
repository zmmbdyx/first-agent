"""优先级任务队列：asyncio 优先级队列，可选 Redis 后端，不可用时纯进程内。

设计权衡
--------
1. **priority 越小越先**（与契约 `RunRequest.priority` 的直觉一致：0 最高）。
   进程内用 `heapq` + 单调递增序号：序号保证"同优先级先进先出"这一稳定性，
   否则堆会比较第二个元素（run_id 字符串）导致顺序看起来随机。
2. **Redis 可选且可选得很彻底**：连接或协议失败立即降级为进程内队列，
   和短期记忆一样遵循"外部依赖不可用不得阻断启动"。Redis 侧用 ZSET，
   复合分数 `priority * 1e12 + seq`（与进程内排序语义完全一致）。
3. **取消是惰性的**：`cancel(run_id)` 只标记，真正丢弃发生在 `get()` 出队时，
   这样取消一个"取到一半"的任务不会破坏堆结构，也能取消尚未入队的 run。
4. 所有对外方法都是协程（`get()` 需要 await 唤醒），非阻塞查询
   （`position/stats/pending`）保持同步签名，方便 API 层直接调用。
"""
from __future__ import annotations

import asyncio
import heapq
import itertools
import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any

from config import Settings

logger = logging.getLogger(__name__)

_KEY_WAIT = "pathforge:queue:wait"
_KEY_CANCEL = "pathforge:queue:cancel"
_SCORE_BASE = 10 ** 12
DEFAULT_PRIORITY = 5
_MIN_PRIORITY = 0
_MAX_PRIORITY = 9


def _clamp_priority(priority: Any) -> int:
    try:
        value = int(priority)
    except (TypeError, ValueError):
        return DEFAULT_PRIORITY
    return max(_MIN_PRIORITY, min(_MAX_PRIORITY, value))


@dataclass(order=True)
class QueueItem:
    """堆元素。排序只看 (priority, seq)，其余字段不参与比较。"""

    priority: int
    seq: int
    run_id: str = field(compare=False)
    payload: dict = field(compare=False, default_factory=dict)
    enqueued_at: float = field(compare=False, default_factory=time.time)

    def score(self) -> float:
        return self.priority * _SCORE_BASE + self.seq

    def to_dict(self) -> dict:
        return {"run_id": self.run_id, "priority": self.priority, "seq": self.seq,
                "payload": self.payload, "enqueued_at": self.enqueued_at}


class PriorityTaskQueue:
    """优先级队列（进程内 heapq 为主，Redis ZSET 可选）。"""

    def __init__(self, cfg: Settings | None = None, redis_url: str | None = None,
                 key_prefix: str = ""):
        self.cfg = cfg
        self.degraded_reason = ""
        self._heap: list[QueueItem] = []
        self._waiting: dict[str, QueueItem] = {}     # run_id -> item（尚未出队）
        self._active: set[str] = set()               # 已出队、处理中
        self._cancelled: set[str] = set()
        self._counter = itertools.count(1)
        self._seq = 0
        self._lock = asyncio.Lock()
        self._has_item = asyncio.Event()
        self._key_wait = (key_prefix or _KEY_WAIT)
        self._key_cancel = (key_prefix.replace(":wait", ":cancel") if key_prefix else _KEY_CANCEL)
        self._redis: Any = None
        self._stats = {"enqueued": 0, "dequeued": 0, "completed": 0, "cancelled": 0,
                       "dropped": 0, "expired": 0}
        url = (redis_url if redis_url is not None
               else str(getattr(cfg, "redis_url", "") or "")) if (cfg or redis_url) else ""
        self._connect((url or "").strip())

    # ---------------- 连接 ----------------
    def _connect(self, url: str) -> None:
        if not url:
            self.degraded_reason = "未配置 REDIS_URL，使用进程内优先级队列"
            return
        try:
            import redis
            client = redis.Redis.from_url(url, socket_connect_timeout=2, socket_timeout=2,
                                          decode_responses=True, protocol=2)
            client.ping()
        except Exception as e:
            self.degraded_reason = f"Redis 不可用（{type(e).__name__}: {e}），已降级为进程内队列"
            logger.info(self.degraded_reason)
            self._redis = None
            return
        self._redis = client

    @property
    def backend_name(self) -> str:
        return "redis" if self._redis is not None else "memory"

    def _next_seq(self) -> int:
        self._seq += 1
        return self._seq

    # ---------------- 入队 / 出队 ----------------
    async def put(self, run_id: str, priority: int = DEFAULT_PRIORITY, **payload: Any) -> QueueItem:
        """入队并返回队列项。同一 `run_id` 重复入队视为更新优先级（幂等）。"""
        rid = str(run_id)
        item = QueueItem(priority=_clamp_priority(priority), seq=self._next_seq(), run_id=rid,
                         payload=dict(payload))
        self._cancelled.discard(rid)
        async with self._lock:
            self._waiting[rid] = item
            if self._redis is not None:
                try:
                    self._redis.zadd(self._key_wait, {json.dumps(item.to_dict(), ensure_ascii=False,
                                                                default=str): item.score()})
                    self._redis.srem(self._key_cancel, rid)
                except Exception as e:
                    self._degrade(e)
            if self._redis is None:
                heapq.heappush(self._heap, item)
            self._stats["enqueued"] += 1
            self._has_item.set()
        return item

    async def get(self, timeout: float | None = None) -> QueueItem | None:
        """取下一个任务（优先级升序）。超时返回 None；跳过已取消的条目。"""
        deadline = None if timeout is None else (time.monotonic() + max(0.0, timeout))
        while True:
            item = await self._pop_once()
            if item is not None:
                if item.run_id in self._cancelled:
                    self._cancelled.discard(item.run_id)
                    self._waiting.pop(item.run_id, None)
                    self._stats["cancelled"] += 1
                    continue
                async with self._lock:
                    self._waiting.pop(item.run_id, None)
                    self._active.add(item.run_id)
                    self._stats["dequeued"] += 1
                return item
            # 队列空：等待唤醒。wait_for 的 timeout=None 表示无限等待，
            # 因此这里显式算出剩余等待时间，超时即返回 None（调用方可重试）。
            if deadline is None:
                remain: float | None = None
            else:
                remain = max(0.0, deadline - time.monotonic())
                if remain <= 0:
                    return None
            try:
                await asyncio.wait_for(self._has_item.wait(), timeout=remain)
            except asyncio.TimeoutError:
                if deadline is not None and time.monotonic() >= deadline:
                    return None
            finally:
                if self._has_item.is_set() and self.pending_count() == 0:
                    # 唤醒信号来自已取消/已取走的条目：清位避免空转
                    self._has_item.clear()

    async def _pop_once(self) -> QueueItem | None:
        async with self._lock:
            if self._redis is not None:
                try:
                    rows = self._redis.zrange(self._key_wait, 0, 0, withscores=True)
                except Exception as e:
                    self._degrade(e)
                    rows = None
                if rows:
                    raw, score = rows[0]
                    self._redis.zrem(self._key_wait, raw)
                    try:
                        data = json.loads(raw)
                    except (TypeError, ValueError):
                        data = {"run_id": str(raw), "priority": int(score // _SCORE_BASE)}
                    return QueueItem(priority=int(data.get("priority", DEFAULT_PRIORITY)),
                                     seq=int(data.get("seq", 0)), run_id=str(data.get("run_id")),
                                     payload=data.get("payload") or {},
                                     enqueued_at=float(data.get("enqueued_at") or time.time()))
                if rows is None:      # 刚降级：走进程内堆
                    pass
                else:
                    self._has_item.clear()
                    return None
            if not self._heap:
                self._has_item.clear()
                return None
            item = heapq.heappop(self._heap)
            if not self._heap:
                self._has_item.clear()
            return item

    def task_done(self, run_id: str) -> bool:
        """标记任务处理完成（清理 active 集合与计数）。"""
        rid = str(run_id)
        was_active = rid in self._active
        self._active.discard(rid)
        self._waiting.pop(rid, None)
        self._cancelled.discard(rid)
        if was_active:
            self._stats["completed"] += 1
        return was_active

    # ---------------- 取消 ----------------
    def cancel(self, run_id: str) -> bool:
        """取消排队中的任务；返回 True 表示该 run 确实在等待或处理中。"""
        rid = str(run_id)
        was_known = rid in self._waiting or rid in self._active
        self._cancelled.add(rid)
        if self._redis is not None:
            try:
                self._redis.sadd(self._key_cancel, rid)
            except Exception as e:
                self._degrade(e)
        self._stats["cancelled"] += 1
        return bool(was_known)

    def is_cancelled(self, run_id: str) -> bool:
        rid = str(run_id)
        if rid in self._cancelled:
            return True
        if self._redis is not None:
            try:
                return bool(self._redis.sismember(self._key_cancel, rid))
            except Exception as e:
                self._degrade(e)
        return False

    # ---------------- 查询 ----------------
    def position(self, run_id: str) -> int:
        """排队位置（1 为队首）；不在队列中或已取消返回 0。"""
        rid = str(run_id)
        if rid in self._cancelled:
            return 0        # 已取消的任务不占位置（否则前端会显示一个永远不动的排队号）
        item = self._waiting.get(rid)
        if item is None:
            return 0
        if self._redis is not None:
            try:
                rank = self._redis.zrank(self._key_wait, json.dumps(item.to_dict(),
                                                                    ensure_ascii=False, default=str))
                if rank is not None:
                    return int(rank) + 1
            except Exception as e:
                self._degrade(e)
        return sum(1 for other in self._waiting.values() if other.score() < item.score()) + 1

    def pending(self) -> list[dict]:
        """排队中的任务摘要（按优先级排序），供 API 展示。"""
        items = sorted(self._waiting.values(), key=lambda i: i.score())
        return [{"run_id": i.run_id, "priority": i.priority, "seq": i.seq,
                 "waiting_s": round(time.time() - i.enqueued_at, 2)} for i in items]

    def pending_count(self) -> int:
        """等待中的任务数（同步、非阻塞）。"""
        return len(self._waiting)

    async def stats(self) -> dict:
        async with self._lock:
            return {"backend": self.backend_name, "waiting": len(self._waiting),
                    "active": len(self._active), "cancelled": len(self._cancelled),
                    "degraded_reason": self.degraded_reason, **self._stats}

    def _degrade(self, exc: Exception) -> None:
        """运行期 Redis 出错 → 永久降级到进程内堆。

        已经在 `_waiting` 里登记的任务（无论当时写到了哪一侧）都仍在进程内可见，
        因此降级不会丢任务；只是队列位置信息退化为本地估计。
        """
        self.degraded_reason = f"Redis 运行期异常（{type(exc).__name__}: {exc}），已降级为进程内队列"
        logger.warning(self.degraded_reason)
        self._redis = None
        known = {item.run_id for item in self._heap}
        for item in self._waiting.values():
            if item.run_id not in known:
                heapq.heappush(self._heap, item)
                known.add(item.run_id)
        if self._heap:
            self._has_item.set()


# 兼容别名：API 层按 `from core.queue import TaskQueue` 导入（历史命名习惯）。
# 保留别名而不是改名，避免同时改动调用方与已有文档。
TaskQueue = PriorityTaskQueue


__all__ = ["PriorityTaskQueue", "TaskQueue", "QueueItem", "DEFAULT_PRIORITY"]
