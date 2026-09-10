"""工作区路由：CRUD（契约 2.5 节）。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session as OrmSession

from db import get_db
from schemas.workspace import WorkspaceCreate, WorkspaceListOut, WorkspaceOut, WorkspaceUpdate
from services import get_workspace_service
from services.workspace_service import WorkspaceError

router = APIRouter(tags=["workspaces"], prefix="/workspaces")


@router.get("", response_model=WorkspaceListOut)
def list_workspaces(db: OrmSession = Depends(get_db)) -> dict:
    return {"items": get_workspace_service().list(db=db)}


@router.post("", response_model=WorkspaceOut, status_code=201)
def create_workspace(payload: WorkspaceCreate, db: OrmSession = Depends(get_db)) -> dict:
    try:
        return get_workspace_service().create(payload.name, payload.path,
                                              payload.description, db=db)
    except WorkspaceError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/{ident}", response_model=WorkspaceOut)
def get_workspace(ident: str, db: OrmSession = Depends(get_db)) -> dict:
    data = get_workspace_service().get(ident, db=db)
    if data is None:
        raise HTTPException(status_code=404, detail=f"工作区不存在: {ident}")
    return data


@router.put("/{ident}", response_model=WorkspaceOut)
def update_workspace(ident: str, payload: WorkspaceUpdate,
                     db: OrmSession = Depends(get_db)) -> dict:
    try:
        data = get_workspace_service().update(ident, db=db, name=payload.name,
                                              path=payload.path,
                                              description=payload.description)
    except WorkspaceError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if data is None:
        raise HTTPException(status_code=404, detail=f"工作区不存在: {ident}")
    return data


@router.delete("/{ident}")
def delete_workspace(ident: str, db: OrmSession = Depends(get_db)) -> dict:
    try:
        ok = get_workspace_service().delete(ident, db=db)
    except WorkspaceError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if not ok:
        raise HTTPException(status_code=404, detail=f"工作区不存在: {ident}")
    return {"ok": True}
