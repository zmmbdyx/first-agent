"""会话路由：CRUD + 历史 + 回滚（契约 2.3 节）。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session as OrmSession

from db import get_db
from schemas.session import (CheckpointIn, HistoryOut, RollbackOut, SessionCreate,
                             SessionListOut, SessionOut, SessionPin, SessionRename)
from services import get_session_service
from services.workspace_service import WorkspaceError

router = APIRouter(tags=["sessions"])


@router.get("/sessions", response_model=SessionListOut)
def list_sessions(workspace: str = "", limit: int = Query(50, ge=1, le=500),
                  offset: int = Query(0, ge=0), q: str = "",
                  db: OrmSession = Depends(get_db)) -> dict:
    items, total = get_session_service().list(workspace=workspace or None, limit=limit,
                                              offset=offset, q=q or None, db=db)
    return {"items": items, "total": total}


@router.post("/sessions", response_model=SessionOut, status_code=201)
def create_session(payload: SessionCreate, db: OrmSession = Depends(get_db)) -> dict:
    return get_session_service().create(title=payload.title, workspace=payload.workspace,
                                        preset=payload.preset, db=db,
                                        permission_mode=payload.permission_mode,
                                        model=payload.model)


@router.get("/sessions/{session_id}", response_model=SessionOut)
def get_session(session_id: str, db: OrmSession = Depends(get_db)) -> dict:
    data = get_session_service().get(session_id, db=db)
    if data is None:
        raise HTTPException(status_code=404, detail=f"会话不存在: {session_id}")
    return data


@router.get("/sessions/{session_id}/history", response_model=HistoryOut)
def session_history(session_id: str, db: OrmSession = Depends(get_db)) -> dict:
    data = get_session_service().history(session_id, db=db)
    if data is None:
        raise HTTPException(status_code=404, detail=f"会话不存在: {session_id}")
    return data


@router.put("/sessions/{session_id}/title", response_model=SessionOut)
def rename_session(session_id: str, payload: SessionRename,
                   db: OrmSession = Depends(get_db)) -> dict:
    data = get_session_service().rename(session_id, payload.title, db=db)
    if data is None:
        raise HTTPException(status_code=404, detail=f"会话不存在: {session_id}")
    return data


@router.put("/sessions/{session_id}/pin", response_model=SessionOut)
def pin_session(session_id: str, payload: SessionPin, db: OrmSession = Depends(get_db)) -> dict:
    data = get_session_service().set_pinned(session_id, payload.pinned, db=db)
    if data is None:
        raise HTTPException(status_code=404, detail=f"会话不存在: {session_id}")
    return data


@router.delete("/sessions/{session_id}")
def delete_session(session_id: str, db: OrmSession = Depends(get_db)) -> dict:
    if not get_session_service().delete(session_id, db=db):
        raise HTTPException(status_code=404, detail=f"会话不存在: {session_id}")
    return {"ok": True}


@router.post("/sessions/{session_id}/rollback", response_model=RollbackOut)
def rollback_session(session_id: str, payload: CheckpointIn) -> dict:
    """回滚到指定检查点（空 checkpoint_id 表示回滚到本 run 起点）。

    依赖图编排侧的 RunService；尚未就绪时返回 503 而不是 500——会话本身仍可读写。
    图编排抛出的领域错误（如「没有可用的检查点」）属于可预期的业务状态，映射为 4xx。
    """
    from api.agent import resolve_run_service
    service, err = resolve_run_service()
    if service is None:
        raise HTTPException(status_code=503, detail=err)
    try:
        result = service.rollback(session_id, payload.run_id or "", payload.checkpoint_id or "")
    except WorkspaceError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except KeyError as e:
        raise HTTPException(status_code=404, detail=f"检查点不存在: {e}")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=_domain_status(e), detail=f"{type(e).__name__}: {e}")
    if not isinstance(result, dict):
        result = {"checkpoint_id": payload.checkpoint_id}
    return {"ok": bool(result.get("ok", True)), "session_id": session_id,
            "checkpoint_id": result.get("checkpoint_id", payload.checkpoint_id),
            "detail": result.get("detail")}


def _domain_status(exc: Exception) -> int:
    """把图编排层的领域异常翻译成 4xx：文案像「不存在/已清理」就是 404，其余按 409。

    这样前端拿到的是「这个检查点没了」而不是「服务端崩了」，避免把可预期状态当成故障告警。
    """
    text = str(exc)
    for marker in ("不存在", "没有可用", "已清理", "not found", "missing"):
        if marker in text:
            return 404
    return 409
