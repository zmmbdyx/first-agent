"""文件路由：工作区文件树 / 内容预览 / 原始字节 / 报告产物（契约 2.6 / 2.7 节）。

安全：所有 `path` 参数都经 `WorkspaceService.resolve_path()` 归一化并校验落在工作区内，
越界（`..`、绝对路径、符号链接逃逸）统一由 `WorkspaceError` → 400 拒绝。
"""
from __future__ import annotations

from fastapi import APIRouter, Query, Request
from fastapi.responses import FileResponse, PlainTextResponse, Response

from schemas.file import FileContentOut, FileTreeOut
from services import get_file_service
from services.file_service import media_type_for
from services.workspace_service import WorkspaceError

router = APIRouter(tags=["files"], prefix="/files")
report_router = APIRouter(tags=["files"])


@router.get("/tree", response_model=FileTreeOut)
def file_tree(workspace: str = "default", path: str = "",
              depth: int = Query(3, ge=1, le=8)) -> dict:
    return get_file_service().tree(workspace=workspace, path=path, depth=depth)


@router.get("/content", response_model=FileContentOut)
def file_content(request: Request, workspace: str = "default", path: str = "",
                 raw: int = 0):
    """文本类返回 JSON；`raw=1` 或 Accept: text/plain 时直接返回纯文本正文。"""
    data = get_file_service().content(workspace=workspace, path=path)
    accept = (request.headers.get("accept") or "").lower()
    if raw or ("text/plain" in accept and "application/json" not in accept):
        return PlainTextResponse(data.get("content") or "",
                                 media_type="text/plain; charset=utf-8")
    return data


@router.get("/raw")
def file_raw(workspace: str = "default", path: str = "") -> Response:
    """原始字节（图片/PDF 内联预览）。注意：加密落盘的文件会先解密再回传。"""
    data, media = get_file_service().raw(workspace, path)
    return Response(content=data, media_type=media,
                    headers={"Content-Disposition": "inline", "Cache-Control": "no-store"})


@report_router.get("/files/report")
def report_file(path: str = Query("", description="data/reports/ 下的相对路径或文件名")):
    """报告产物下载：**只放行 data/reports/ 白名单**。

    旧版把项目根整体挂成静态目录，导致 `.env`、`data/.key`、会话存档可被直接下载；
    这里改为按白名单解析 + 二次 resolve 校验（详见 FileService.report_path）。
    """
    target = get_file_service().report_path(path)
    return FileResponse(str(target), media_type=media_type_for(target),
                        filename=target.name)


@router.get("/report-exists")
def report_exists(path: str = Query("")) -> dict:
    """前端探测报告是否已生成（不存在返回 exists=false，不报错）。"""
    try:
        get_file_service().report_path(path)
        return {"exists": True, "path": path}
    except WorkspaceError:
        return {"exists": False, "path": path}


__all__ = ["router", "report_router"]
