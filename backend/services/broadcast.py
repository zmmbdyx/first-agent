"""进程内事件广播中心：WebSocket 实时推送与 SSE 主链路解耦。

为什么需要它：
- SSE 由 `/api/agent/run` 单连接独占，工具调用的亚状态（running/ok/error）与实时指标
  需要在同一时刻推给同一会话的**多个** WS 连接（含断线重连后的新连接）；
- 图编排侧（`services/run_service.py`）在后台线程/任务里产出事件，因此 `broadcast()`
  必须能在任意线程调用——这里用 `asyncio.run_coroutine_threadsafe` 投递，绝不阻塞调用方；
- 队列有界且满时丢弃最旧消息：实时推送宁可丢帧，也不能让慢客户端把运行线程拖死。
"""
from __future__ import annotations

import asyncio
from typing import Any, Dict, List, Optional

# 单连接待发队列上限：够容纳一次工具调用的密集事件，又不至于在客户端卡死时堆积内存
QUEUE_MAX = 256


class EventBroadcaster:
    """按 session_id 分组的订阅/广播；线程安全（订阅表由锁保护）。"""

    def __init__(self) -> None:
        self._subs: Dict[str, List[asyncio.Queue]] = {}
        self._loop: Optional[asyncio.AbstractEventLoop] = None

    # ---------------- 事件循环绑定 ----------------
    def bind_loop(self, loop: Optional[asyncio.AbstractEventLoop] = None) -> None:
        """记录当前事件循环，供非事件循环线程调用 broadcast 时投递。"""
        try:
            self._loop = loop or asyncio.get_running_loop()
        except RuntimeError:
            self._loop = None

    # ---------------- 订阅管理 ----------------
    def subscribe(self, session_id: str, queue: Optional[asyncio.Queue] = None) -> asyncio.Queue:
        q = queue or asyncio.Queue(maxsize=QUEUE_MAX)
        self._subs.setdefault(str(session_id), []).append(q)
        return q

    def unsubscribe(self, session_id: str, queue: asyncio.Queue) -> None:
        subs = self._subs.get(str(session_id))
        if not subs:
            return
        try:
            subs.remove(queue)
        except ValueError:
            pass
        if not subs:
            self._subs.pop(str(session_id), None)

    def subscriber_count(self, session_id: str) -> int:
        return len(self._subs.get(str(session_id), []))

    # ---------------- 广播 ----------------
    def _offer(self, queue: asyncio.Queue, payload: Any) -> None:
        try:
            queue.put_nowait(payload)
        except asyncio.QueueFull:
            # 丢最旧的：新事件比旧事件有价值（轨迹/指标是增量快照）
            try:
                queue.get_nowait()
                queue.put_nowait(payload)
            except Exception:
                pass
        except Exception:
            pass

    def broadcast(self, session_id: str, payload: Any) -> int:
        """向某会话的全部订阅者推送一条事件，返回实际投递数（无订阅者为 0）。"""
        subs = list(self._subs.get(str(session_id), []))
        if not subs:
            return 0
        loop = self._loop
        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            running = None
        sent = 0
        for q in subs:
            try:
                if running is not None:
                    # 已在事件循环内：直接投递即可
                    self._offer(q, payload)
                elif loop is not None and not loop.is_closed():
                    loop.call_soon_threadsafe(self._offer, q, payload)
                else:
                    continue
                sent += 1
            except RuntimeError:
                continue  # 循环已关闭（进程收尾阶段），静默忽略
        return sent

    def broadcast_all(self, payload: Any) -> int:
        return sum(self.broadcast(sid, payload) for sid in list(self._subs))


# 全局单例：run_service 与 api/ws.py 共用同一实例才能互通
broadcaster = EventBroadcaster()


def broadcast(session_id: str, payload: Any) -> int:
    """模块级便捷入口（契约约定 run_service 调用 `broadcast(session_id, payload)`）。"""
    return broadcaster.broadcast(session_id, payload)


def subscribe(session_id: str, queue: Optional[asyncio.Queue] = None) -> asyncio.Queue:
    return broadcaster.subscribe(session_id, queue)


def unsubscribe(session_id: str, queue: asyncio.Queue) -> None:
    broadcaster.unsubscribe(session_id, queue)


def bind_loop(loop: Optional[asyncio.AbstractEventLoop] = None) -> None:
    broadcaster.bind_loop(loop)


__all__ = ["EventBroadcaster", "broadcaster", "broadcast", "subscribe", "unsubscribe",
           "bind_loop", "QUEUE_MAX"]
