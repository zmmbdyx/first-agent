"""工具路由：列表 / 注册 / 卸载 / 试调用（契约 2.4 节，MCP 兼容）。"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from schemas.tool import ToolListOut, ToolOut, ToolRegister, ToolTestIn, ToolTestOut
from services import get_mcp_service, get_tool_service

router = APIRouter(tags=["tools"], prefix="/tools")


@router.get("", response_model=ToolListOut)
def list_tools() -> dict:
    tools = get_tool_service()
    return {"items": tools.list(), "mcp": get_mcp_service().info()}


@router.post("", response_model=ToolOut, status_code=201)
def register_tool(payload: ToolRegister) -> dict:
    try:
        return get_tool_service().register(payload.model_dump())
    except FileExistsError as e:
        raise HTTPException(status_code=409, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.delete("/{name}")
def unregister_tool(name: str) -> dict:
    tools = get_tool_service()
    if tools.is_builtin(name) and name in tools.registry().tools:
        # 内置工具是业务核心能力，不允许通过接口卸载
        raise HTTPException(status_code=403, detail=f"内置工具不可卸载: {name}")
    if not tools.unregister(name):
        raise HTTPException(status_code=404, detail=f"自定义工具不存在: {name}")
    return {"ok": True, "name": name}


@router.post("/{name}/test", response_model=ToolTestOut)
def test_tool(name: str, payload: ToolTestIn) -> dict:
    try:
        return get_tool_service().test(name, payload.args)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"工具不存在: {name}")
