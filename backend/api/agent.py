"""Agent 执行路由：SSE 事件流、中断、run 状态、预设列表（契约 2.1 / 2.2 / 2.7）。

关键容错：图编排侧 `services/run_service.py` 由并行工作流提供，可能尚未落地。
因此 `RunService` 只能**惰性导入**（放在函数内部），导入失败时返回 503 + 明确 detail，
保证服务能正常启动、其余端点照常可用。
"""
from __future__ import annotations

import asyncio
import json
import threading
import time
from typing import Any, AsyncIterator, Dict, List, Optional, Tuple

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse

from schemas.agent import InterruptOut, PresetListOut, RunRequest, RunStatusOut
from security import current_owner
from services import get_session_service
from services.run_service import QueueFullError, RunServiceError

router = APIRouter(tags=["agent"], prefix="/agent")

# 预设兜底定义：与契约 5.4 的原创文案一致（core/presets.py 到位后以它为准）
FALLBACK_PRESETS: List[dict] = [
    {"id": "standard", "name": "标准", "description": "规划 → 执行 → 综合报告，通用默认档",
     "params": {"max_react_steps": 5, "max_tasks": 6, "permission_mode": "workspace_write"}},
    {"id": "minimal", "name": "极简", "description": "更少步骤与工具调用，快速给出结论",
     "params": {"max_react_steps": 2, "max_tasks": 3}},
    {"id": "ptc", "name": "PTC", "description": "规划–工具–校验：每个子任务执行后强制校验",
     "params": {"validate": True, "max_react_steps": 4}},
    {"id": "creative", "name": "创造", "description": "提高采样自由度，鼓励多方案发散",
     "params": {"temperature": 0.8, "max_tasks": 8}},
]

_run_lock = threading.Lock()
_run_cache: Dict[str, Any] = {"service": None, "error": "", "tried": False}

RUN_SERVICE_HINT = ("图编排服务未就绪：services/run_service.py 尚未提供或初始化失败（{err}）。"
                    "其余接口不受影响；该模块落地后本端点自动可用。")


def resolve_run_service() -> Tuple[Optional[Any], str]:
    """惰性构造 RunService（只尝试一次，避免每个请求重复踩同一个 ImportError）。"""
    with _run_lock:
        if _run_cache["tried"]:
            return _run_cache["service"], _run_cache["error"]
        _run_cache["tried"] = True
    service, err = None, ""
    try:
        from config import load_config
        from services import get_tool_service
        from services.run_service import RunService  # 惰性导入：模块可能尚未落地
        queue = _optional_queue()
        kwargs = {} if queue is None else {"queue": queue}
        service = RunService(load_config(), get_session_service(), get_tool_service(), **kwargs)
    except Exception as e:
        err = f"{type(e).__name__}: {e}"
        print(f"[api.agent] RunService 不可用（{err}），/api/agent/run 将返回 503")
    with _run_lock:
        _run_cache["service"], _run_cache["error"] = service, err
    return service, err


def _optional_queue() -> Optional[Any]:
    """优先级队列（core/queue.py）就绪时注入；未就绪则让 RunService 用默认实现。"""
    try:
        from core.queue import TaskQueue  # type: ignore[attr-defined]
        return TaskQueue()
    except Exception:
        return None


def sse_frame(event: str, payload: Dict[str, Any]) -> str:
    """契约格式：`event: <type>\\ndata: <单行JSON>\\n\\n`，data 必须单行（ensure_ascii=False）。"""
    body = json.dumps(payload, ensure_ascii=False, default=str).replace("\n", "\\n")
    return f"event: {event}\ndata: {body}\n\n"


def sse_headers() -> Dict[str, str]:
    return {"Cache-Control": "no-cache", "X-Accel-Buffering": "no",
            "Connection": "keep-alive"}


async def sse_stream(service: Any, req: RunRequest, heartbeat: float = 15.0,
                     owner: str = "default") -> AsyncIterator[str]:
    """把 RunService 的事件流包装成 SSE；15s 无事件即发 heartbeat 保活。

    心跳与业务事件并发等待，任一先到即产出——这样长耗时工具调用也不会让
    浏览器/反向代理误判连接已死。同时兼容 `stream()` 返回同步/异步迭代器两种实现。
    """
    try:
        events = service.stream(req, owner=owner)
    except TypeError:                  # 兼容尚未支持 owner 参数的实现
        events = service.stream(req)
    if hasattr(events, "__await__"):
        events = await events
    iterator = events.__aiter__() if hasattr(events, "__aiter__") else _thread_iter(events)
    recorder = EventRecorder()  # 事件落库（tool_calls / trajectory_nodes），失败不影响转发
    while True:
        try:
            item = await asyncio.wait_for(iterator.__anext__(), timeout=heartbeat)
        except StopAsyncIteration:
            return
        except asyncio.TimeoutError:
            yield sse_frame("heartbeat", {"type": "heartbeat", "ts": time.time()})
            continue
        if item is None:
            continue
        if isinstance(item, str):
            yield item if item.endswith("\n\n") else sse_frame("message", {"type": "message",
                                                                          "data": item})
            continue
        if not isinstance(item, dict):
            continue
        etype = str(item.get("type") or "message")
        recorder.on_event(item)
        yield sse_frame(etype, item)


class _ThreadIter:
    """同步迭代器 → 异步迭代器：每次 next() 丢到线程池，避免阻塞事件循环。"""

    def __init__(self, iterable: Any) -> None:
        self._it = iter(iterable)

    def __aiter__(self) -> "_ThreadIter":
        return self

    async def __anext__(self) -> Any:
        try:
            return await asyncio.to_thread(next, self._it)
        except StopIteration:
            raise StopAsyncIteration


def _thread_iter(iterable: Any) -> _ThreadIter:
    return _ThreadIter(iterable)


class EventRecorder:
    """把流经 SSE 的契约事件落库：tool_calls 与 trajectory_nodes 两张表的写入方。

    为什么放在这一层：图编排只负责「产出事件」，持久化属于数据层职责；这里复用
    同一条事件流，避免让 run_service 再依赖一遍数据库表结构。
    任何写入失败都只提示一次并继续转发——**落库问题绝不能中断用户可见的输出**。
    """

    def __init__(self) -> None:
        self.run_id = ""
        self.session_id = ""
        self.warned = False
        # 图编排的 node_end 不重复携带 seq（只有 node_id），这里用 node_start 建立映射，
        # 否则所有结束事件都会落到 seq=0 上互相覆盖，轨迹表只剩一行。
        self._node_seq: Dict[str, int] = {}
        self._node_meta: Dict[str, Dict[str, Any]] = {}
        # 实时指标累计：run_done 一到达就落库，前端随后查历史/统计立刻有数据，
        # 不必等图编排侧的后台收尾任务（那一步可能晚于连接关闭）
        self.stats: Dict[str, Any] = {}
        self.tool_call_count = 0
        self.finished = False

    def on_event(self, event: Dict[str, Any]) -> None:
        etype = str(event.get("type") or "")
        self.run_id = str(event.get("run_id") or self.run_id)
        self.session_id = str(event.get("session_id") or self.session_id)
        if not (self.run_id or self.session_id):
            return
        try:
            if etype == "tool_call":
                self._tool_call(event)
            elif etype in ("tool_result", "tool_error"):
                self._tool_done(event, ok=(etype == "tool_result"))
            elif etype == "node_start":
                self._node(event, status="running")
            elif etype == "node_end":
                self._node(event, status=str(event.get("status") or "done"))
            elif etype == "metric":
                self.stats.update({k: v for k, v in event.items()
                                   if k not in ("type", "session_id", "ts")})
            elif etype in ("final_answer", "run_done"):
                self._run_done(event)
        except Exception as e:
            if not self.warned:  # 一次流只提醒一次，避免日志刷屏
                self.warned = True
                print(f"[api.agent] 轨迹/工具调用落库失败（不影响输出）: {type(e).__name__}: {e}")

    # ---------------- 具体事件 ----------------
    def _tool_call(self, event: Dict[str, Any]) -> None:
        service = get_session_service()
        self.tool_call_count += 1
        service.record_tool_call(
            run_id=self.run_id, session_id=self.session_id,
            task_id=str(event.get("task_id") or ""), tool=str(event.get("tool") or ""),
            args=event.get("args") or {}, brief="", ok=True,
            call_id=str(event.get("call_id") or ""),
            tokens={"cost": event.get("cost")} if event.get("cost") else None)

    def _tool_done(self, event: Dict[str, Any], ok: bool) -> None:
        service = get_session_service()
        call_id = str(event.get("call_id") or "")
        fields = {"brief": str(event.get("brief") or ""), "ok": ok,
                  "error": str(event.get("error") or ""),
                  "elapsed_ms": int(event.get("elapsed_ms") or 0),
                  "tokens": event.get("tokens")}
        if call_id and service.update_tool_call(call_id, **fields) is not None:
            return
        # 没有 call_id（或开始事件丢帧）：直接补一条完整记录，避免统计缺项
        service.record_tool_call(
            run_id=self.run_id, session_id=self.session_id,
            task_id=str(event.get("task_id") or ""), tool=str(event.get("tool") or ""),
            brief=fields["brief"], ok=ok, error=fields["error"],
            elapsed_ms=fields["elapsed_ms"], tokens=fields["tokens"])

    def _node(self, event: Dict[str, Any], status: str) -> None:
        node_id = str(event.get("node_id") or "")
        seq = event.get("seq")
        if seq is not None:
            seq = int(seq or 0)
            if node_id:
                self._node_seq[node_id] = seq
                self._node_meta[node_id] = {"node": event.get("node"), "label": event.get("label")}
        else:
            seq = self._node_seq.get(node_id, 0)
        meta = self._node_meta.get(node_id) or {}
        get_session_service().record_node(
            run_id=self.run_id, session_id=self.session_id,
            node=str(event.get("node") or meta.get("node") or ""),
            label=str(event.get("label") or meta.get("label") or ""),
            seq=seq, status=status,
            elapsed_ms=int(event.get("elapsed_ms") or 0),
            tokens=event.get("tokens") if isinstance(event.get("tokens"), dict) else None,
            error=str(event.get("error") or ""))

    def _run_done(self, event: Dict[str, Any]) -> None:
        """run_done/final_answer 到达即归档：状态置 done 并把累计指标写进 runs.stats。

        为什么不等收尾任务：SSE 连接一关闭，图编排的后台落库可能还没跑到，
        此时前端立刻查历史就会看到空统计。这里先写一版，收尾任务再覆盖为最终值。
        """
        if isinstance(event.get("stats"), dict):
            self.stats.update(event["stats"])
        if self.finished or not self.run_id:
            return
        self.finished = True
        stats = dict(self.stats)
        stats.setdefault("tool_calls", self.tool_call_count)
        stats.setdefault("node_count", len(self._node_seq))
        stats.setdefault("tool_call_records", self.tool_call_count)
        get_session_service().update_run(self.run_id, status="done", stats=stats)


@router.post("/run")
async def run_agent(req: RunRequest, owner: str = Depends(current_owner)):
    """`POST /api/agent/run` → text/event-stream（契约 2.1 / 2.2）。

    队列满时必须在**建立 SSE 之前**返回 429：流一旦开始输出就无法再改状态码，
    否则前端只会看到一条"错误事件"而不是可重试的限流信号。
    """
    service, err = resolve_run_service()
    if service is None:
        raise HTTPException(status_code=503, detail=RUN_SERVICE_HINT.format(err=err or "未知原因"))
    try:
        service.precheck(req)          # 队列容量 / 空任务等快速失败检查
    except QueueFullError as e:
        raise HTTPException(status_code=429, detail=str(e),
                            headers={"Retry-After": "5"})
    except RunServiceError as e:
        raise HTTPException(status_code=400, detail=str(e))
    try:
        return StreamingResponse(sse_stream(service, req, owner=owner),
                                 media_type="text/event-stream", headers=sse_headers())
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"SSE 流初始化失败: {type(e).__name__}: {e}")


@router.post("/runs/{run_id}/interrupt", response_model=InterruptOut)
def interrupt_run(run_id: str) -> dict:
    service, err = resolve_run_service()
    if service is None:
        raise HTTPException(status_code=503, detail=RUN_SERVICE_HINT.format(err=err or "未知原因"))
    try:
        ok = bool(service.interrupt(run_id))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"{type(e).__name__}: {e}")
    if not ok:
        raise HTTPException(status_code=404, detail=f"运行不存在或已结束: {run_id}")
    return {"ok": True, "run_id": run_id}


@router.get("/runs/{run_id}", response_model=RunStatusOut)
def run_status(run_id: str) -> dict:
    """run 状态与统计：优先走 RunService，其次回落数据库中的 runs 记录。"""
    service, _ = resolve_run_service()
    if service is not None:
        try:
            data = service.status(run_id)
            if data:
                return _normalize_status(data, run_id)
        except Exception:
            pass  # 图编排层异常不应让状态查询整体失败，下面用库里的记录兜底
    row = get_session_service().get_run(run_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"运行不存在: {run_id}")
    return _normalize_status(row, run_id)


@router.get("/presets", response_model=PresetListOut)
def list_presets() -> dict:
    """预设列表：优先读 `core/presets.py`（并行工作流提供），未就绪时用契约兜底定义。"""
    try:
        from core import presets as presets_mod  # type: ignore[attr-defined]
        items = _presets_from_module(presets_mod)
        if items:
            return {"items": items}
    except Exception:
        pass
    return {"items": FALLBACK_PRESETS}


def _presets_from_module(module: Any) -> List[dict]:
    """兼容多种形态：PRESETS 常量 / dict / list_presets()，以及 Preset 数据类对象。

    `core/presets.py` 由并行工作流提供，形态未定；这里做的是**宽松归一**：
    只要对象带 id + name 就认，绝不因为字段多寡而丢掉整个预设列表。
    """
    raw = getattr(module, "PRESETS", None)
    if raw is None:
        for fn_name in ("list_presets", "get_presets", "all_presets"):
            fn = getattr(module, fn_name, None)
            if callable(fn):
                try:
                    raw = fn()
                    break
                except Exception:
                    raw = None
    if isinstance(raw, dict):
        raw = [{"id": k, **(v if isinstance(v, dict) else _obj_fields(v))}
               for k, v in raw.items()]
    if not isinstance(raw, (list, tuple)):
        return []
    items: List[dict] = []
    for p in raw:
        data = p if isinstance(p, dict) else _obj_fields(p)
        if not data.get("id"):
            continue
        items.append({"id": str(data["id"]), "name": str(data.get("name") or data["id"]),
                      "description": str(data.get("description") or ""),
                      "params": dict(data.get("params") or {})})
    return items


def _obj_fields(obj: Any) -> Dict[str, Any]:
    """dataclass / pydantic 对象 → 字段 dict（含槽位对象）。"""
    for attr in ("model_dump", "dict"):
        fn = getattr(obj, attr, None)
        if callable(fn):
            try:
                data = fn()
                if isinstance(data, dict):
                    return data
            except Exception:
                pass
    if hasattr(obj, "__dict__") and getattr(obj, "__dict__", None):
        return dict(obj.__dict__)
    # 兜底：仅暴露已知字段，避免把内部状态一并带出
    return {k: getattr(obj, k) for k in ("id", "name", "description", "params")
            if hasattr(obj, k)}


def _normalize_status(data: Any, run_id: str) -> dict:
    """把 RunService/数据库两种来源归一成 RunStatusOut 字段。"""
    if not isinstance(data, dict):
        return {"run_id": run_id, "status": "unknown"}
    if "run_id" not in data:
        data = {**data, "run_id": run_id}
    for key in ("started_at", "finished_at"):
        value = data.get(key)
        if value is not None and not isinstance(value, str):
            data[key] = str(value)
    data.setdefault("stats", {})
    return data
