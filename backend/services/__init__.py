"""服务层包：业务逻辑与数据访问的唯一入口，路由层只做协议转换与错误映射。

`service()` 工厂按需构造并缓存服务实例：
- `ToolService` 内部会创建一个 JobAgent（加载 9 个内置工具 + 工具缓存），
  是**重对象**，必须复用，否则每个请求都会重建一次工具箱；
- 其余服务是轻量的无状态壳，缓存只为少读一次配置。
"""
from __future__ import annotations

import threading
from typing import Dict, Optional

from services.broadcast import EventBroadcaster, broadcast, broadcaster
from services.file_service import FileService
from services.git_service import GitService
from services.mcp_service import MCPService
from services.session_service import SessionService
from services.tool_service import ToolService
from services.workspace_service import WorkspaceError, WorkspaceService

_lock = threading.Lock()
_cache: Dict[str, object] = {}


def service(name: str):
    """取（或构造）单例服务；name ∈ session/workspace/file/git/tool/mcp。"""
    inst = _cache.get(name)
    if inst is not None:
        return inst
    with _lock:
        inst = _cache.get(name)
        if inst is not None:
            return inst
        if name == "session":
            inst = SessionService()
        elif name == "workspace":
            inst = WorkspaceService()
        elif name == "file":
            inst = FileService(workspaces=service("workspace"))
        elif name == "git":
            inst = GitService(workspaces=service("workspace"))
        elif name == "tool":
            inst = ToolService()
        elif name == "mcp":
            inst = MCPService(service("tool"))
        else:
            raise KeyError(f"未知服务: {name}")
        _cache[name] = inst
        return inst


def get_session_service() -> SessionService:
    return service("session")


def get_workspace_service() -> WorkspaceService:
    return service("workspace")


def get_file_service() -> FileService:
    return service("file")


def get_git_service() -> GitService:
    return service("git")


def get_tool_service() -> ToolService:
    return service("tool")


def get_mcp_service() -> MCPService:
    return service("mcp")


def optional(name: str) -> Optional[object]:
    """可选依赖的安全取用（构造失败返回 None，路由层据此降级）。"""
    try:
        return service(name)
    except Exception as e:
        print(f"[services] {name} 初始化失败，已降级: {type(e).__name__}: {e}")
        return None


__all__ = [
    "SessionService", "WorkspaceService", "FileService", "GitService", "ToolService",
    "MCPService", "EventBroadcaster", "WorkspaceError", "broadcaster", "broadcast",
    "service", "optional", "get_session_service", "get_workspace_service",
    "get_file_service", "get_git_service", "get_tool_service", "get_mcp_service",
]
