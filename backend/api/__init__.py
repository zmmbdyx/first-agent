"""API 路由层：只做协议转换（Pydantic 校验、错误码映射、SSE/WS 帧），业务逻辑全在 services。

统一约定：
- 路径越界 / 非法参数 → 400（`WorkspaceError` 由 `main.py` 的全局异常处理器翻译为
  `{"detail": "..."}`，这样服务层的 ValueError 家族不必在每个路由里重复捕获）；
- 资源不存在 → 404；名称冲突 → 409；内置资源不可删 → 403；
- 可选依赖（图编排 `services/run_service.py`）缺失 → 503，且绝不影响其余端点。
"""
from __future__ import annotations

from fastapi import APIRouter

from api.agent import router as agent_router
from api.assets import health_router, router as assets_router
from api.files import report_router, router as files_router
from api.git import router as git_router
from api.sessions import router as sessions_router
from api.tools import router as tools_router
from api.workspaces import router as workspaces_router
from api.ws import router as ws_router

router = APIRouter(prefix="/api")
router.include_router(sessions_router)
router.include_router(agent_router)
router.include_router(tools_router)
router.include_router(workspaces_router)
router.include_router(files_router)
router.include_router(report_router)
router.include_router(git_router)
router.include_router(assets_router)
router.include_router(health_router)

# WebSocket 不带 /api 前缀（契约：/ws/agent/{session_id}）
ws = ws_router

__all__ = ["router", "ws"]
