"""Git 路由：工作区变更 / stage / unstage（契约 2.6 节）。"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from schemas.file import GitStageIn, GitStageOut, GitStatusOut
from services import get_git_service

router = APIRouter(tags=["git"], prefix="/git")


@router.get("/status", response_model=GitStatusOut)
def git_status(workspace: str = "default") -> dict:
    """非 Git 仓库 / 未安装 git 都返回 is_repo=false 且 changes 为空（不是错误）。"""
    return get_git_service().status(workspace)


@router.post("/stage", response_model=GitStageOut)
def git_stage(payload: GitStageIn) -> dict:
    if not payload.paths:
        raise HTTPException(status_code=400, detail="paths 不能为空")
    changes = get_git_service().stage(payload.workspace, payload.paths)["changes"]
    return {"ok": True, "changes": changes}


@router.post("/unstage", response_model=GitStageOut)
def git_unstage(payload: GitStageIn) -> dict:
    if not payload.paths:
        raise HTTPException(status_code=400, detail="paths 不能为空")
    changes = get_git_service().unstage(payload.workspace, payload.paths)["changes"]
    return {"ok": True, "changes": changes}
