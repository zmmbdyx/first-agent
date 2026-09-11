"""WebSocket 路由：`/ws/agent/{session_id}` 工具调用状态与指标的实时推送（契约 2.8 节）。

设计要点：
- WS 只是 SSE 主链路的**旁路加速通道**：连接/断开/推送失败都不影响 SSE 执行，
  事件源是 `services/broadcast.py` 的进程内广播中心，`run_service` 只管往里投递；
- 接收与发送分离在两个任务里：发送端可能被慢客户端阻塞，绝不能让接收端（ping/pong）停摆。
"""
from __future__ import annotations

import asyncio
from typing import Any, Dict

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from security import authorize_websocket
from services.broadcast import broadcaster, bind_loop

router = APIRouter(tags=["ws"])

# 单连接发送队列上限（与广播中心一致，慢客户端只丢帧不断链）
SEND_QUEUE_MAX = 256


async def _send_loop(websocket: WebSocket, queue: asyncio.Queue) -> None:
    while True:
        payload = await queue.get()
        if payload is None:
            return
        try:
            await websocket.send_json(payload)
        except Exception:
            return  # 连接已断：静默退出，由接收端负责清理


@router.websocket("/ws/agent/{session_id}")
async def agent_ws(websocket: WebSocket, session_id: str) -> None:
    # 鉴权必须发生在 accept 之前：先 accept 再关闭会先建立连接、白名单一次握手开销，
    # 且前端拿不到明确的失败原因。令牌由 `?token=` 传入（浏览器 WS 不能自定义请求头）。
    owner = authorize_websocket(websocket)
    if owner is None:
        await websocket.close(code=4401)   # 4401：自定义"未授权"码，前端据此提示
        return
    # 会话归属校验：别人的 session_id 不允许订阅（否则会收到他人运行的实时事件）
    try:
        from services import get_session_service
        row = get_session_service().get(session_id, owner=owner)
        if row is None and session_id not in ("", "adhoc"):
            await websocket.close(code=4403)
            return
    except Exception:  # noqa: BLE001 — 校验失败按拒绝处理（fail-closed）
        await websocket.close(code=4403)
        return

    await websocket.accept()
    bind_loop()
    queue = broadcaster.subscribe(session_id)
    sender = asyncio.create_task(_send_loop(websocket, queue))
    try:
        while True:
            message: Dict[str, Any] = await websocket.receive_json()
            mtype = str((message or {}).get("type") or "")
            if mtype == "ping":
                await websocket.send_json({"type": "pong"})
            elif mtype == "subscribe":
                # 单连接天然只订阅本会话；回执带上 run_id 让前端确认订阅生效
                await websocket.send_json({"type": "subscribed", "session_id": session_id,
                                           "run_id": message.get("run_id", "")})
            else:
                await websocket.send_json({"type": "error",
                                           "message": f"不支持的消息类型: {mtype or '(空)'}"})
    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        broadcaster.unsubscribe(session_id, queue)
        sender.cancel()
        try:
            await websocket.close()
        except Exception:
            pass


__all__ = ["router"]
