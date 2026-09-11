"""认证与归属者（owner）解析 —— 对应评审 P0-1 / P0-2。

设计取舍：
1. **默认关闭**（AUTH_ENABLED=false）：本地单机开箱即跑，行为与加固前完全一致；
   一旦对公网或多用户暴露，只需置 true 并配置令牌，无需改代码。
2. **失败关闭（fail-closed）**：开关打开却没配任何令牌时，视为配置错误——
   所有请求一律拒绝，并在启动日志里告警。宁可服务不可用，也不要"看起来开了鉴权其实全放行"。
3. **中间件统一拦截 + 依赖按需取 owner**：中间件对 `/api/*` 与 `/metrics` 做全局校验
   （新增路由默认受保护，不会因为忘记加依赖而裸奔）；需要按用户隔离数据的路由
   通过 `current_owner` 依赖读取 `request.state.owner`。
4. 令牌比较使用 `secrets.compare_digest`，避免时序侧信道。
"""
from __future__ import annotations

import secrets
from typing import Optional

from fastapi import HTTPException, Request, WebSocket, status

from config import Settings, load_config

# 无论是否开启鉴权都放行的路径：健康检查用于容器探针与前端连接状态
PUBLIC_PATHS = {"/api/health"}
# 开启鉴权后需要一并保护的"信息面"：接口文档会完整枚举攻击面
DOC_PATHS = {"/docs", "/redoc", "/openapi.json", "/docs/oauth2-redirect"}

DEFAULT_OWNER = "default"


def _cfg() -> Settings:
    return load_config()


def extract_token(headers, query_token: str = "") -> str:
    """从请求头取令牌：`Authorization: Bearer x` 或 `X-API-Key: x`（两种都接受）。

    WebSocket 无法自定义请求头，因此额外允许 `?token=` 查询参数。
    """
    def _get(name: str) -> str:
        try:
            return (headers.get(name) or "").strip()
        except Exception:  # noqa: BLE001 — 不同实现的 headers 接口略有差异
            return ""

    raw = _get(_cfg().auth_header) or _get("Authorization") or _get("X-API-Key") or _get("x-api-key")
    if raw.lower().startswith("bearer "):
        raw = raw[7:].strip()
    return raw or (query_token or "").strip()


def resolve_owner(token: str, cfg: Optional[Settings] = None) -> Optional[str]:
    """令牌 → owner；返回 None 表示令牌无效。未开启鉴权时一律 default。"""
    cfg = cfg or _cfg()
    if not cfg.auth_required:
        return DEFAULT_OWNER
    key_map = cfg.key_map
    if not key_map:                      # 配置错误：拒绝一切（fail-closed）
        return None
    for known, owner in key_map.items():
        if secrets.compare_digest(known, token):
            return owner
    return None


def is_public(path: str, cfg: Optional[Settings] = None) -> bool:
    """该路径是否无需鉴权。关闭鉴权时全部放行。"""
    cfg = cfg or _cfg()
    if not cfg.auth_required:
        return True
    p = (path or "").rstrip("/") or "/"
    if p in PUBLIC_PATHS:
        return True
    if p in DOC_PATHS:
        return False                     # 开启鉴权后文档也要令牌
    # 非 /api、非 /metrics 的路径是前端静态资源与 SPA 回退：不含数据，放行
    return not (p.startswith("/api") or p == "/metrics")


def authorize_request(request: Request) -> str:
    """FastAPI 中间件调用：校验并返回 owner；失败抛 HTTPException。

    令牌来源：请求头优先；**仅对 GET/HEAD** 额外接受 `?token=`。
    为什么允许查询参数：`<img src>` / `<iframe src>` / 浏览器直开下载链接无法携带自定义头，
    若一律拒绝，开启鉴权后图片与 PDF 预览、报告直链会全部 401。
    代价是令牌可能进入访问日志与浏览器历史，因此：
      ① 只读方法才接受，写操作必须走请求头（避免 CSRF 式的"链接即操作"）；
      ② 正常前端路径仍走请求头，查询参数只作直链兜底。
    """
    cfg = _cfg()
    if not cfg.auth_required:
        return DEFAULT_OWNER
    query_token = ""
    if request.method in ("GET", "HEAD"):
        query_token = (request.query_params.get("token") or "").strip()
    token = extract_token(request.headers, query_token)
    owner = resolve_owner(token, cfg)
    if owner is None:
        # 区分"没带凭据"与"凭据不对"：前者 401 提示补凭据，后者 403 提示凭据无效
        if not token:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED,
                                detail="未提供访问令牌（请在设置中填写，或用 Authorization: Bearer）",
                                headers={"WWW-Authenticate": "Bearer"})
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="访问令牌无效")
    return owner


def authorize_websocket(websocket: WebSocket) -> Optional[str]:
    """WebSocket 鉴权：通过返回 owner，失败返回 None（调用方负责 close）。"""
    cfg = _cfg()
    if not cfg.auth_required:
        return DEFAULT_OWNER
    token = extract_token(websocket.headers, websocket.query_params.get("token", ""))
    return resolve_owner(token, cfg)


def current_owner(request: Request) -> str:
    """FastAPI 依赖：取当前请求的 owner。

    中间件已做过校验并写入 `request.state.owner`；这里只是读取，
    未经过中间件（如单元测试直接调用路由）时回落到 default，避免 500。
    """
    return getattr(request.state, "owner", DEFAULT_OWNER)


__all__ = ["PUBLIC_PATHS", "DOC_PATHS", "DEFAULT_OWNER", "extract_token", "resolve_owner",
           "is_public", "authorize_request", "authorize_websocket", "current_owner"]
