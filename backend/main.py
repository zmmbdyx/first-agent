"""FastAPI 入口：CORS、生命周期（建表 / 默认工作区 / 广播中心）、路由挂载、
静态站点托管（存在 `frontend/dist` 时挂载并做 SPA 回退）。

启动约束（契约要求「无 PostgreSQL / Redis 也必须能跑」）：
- lifespan 中任何一步失败都只打印降级日志，绝不抛异常终止进程；
- `/api/health` 如实上报每一项的实际可用状态，便于定位降级原因。
"""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles

from config import PROJECT_ROOT, load_config
from services.workspace_service import NotFoundError, WorkspaceError

import observability as obs

cfg = load_config()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """启动准备：建表 → 默认工作区 → 绑定事件循环；全部失败可降级。"""
    from db import init_db, ping
    try:
        init_db()
        print(f"[startup] 数据库就绪: {'连接正常' if ping() else '不可用（相关接口会报错）'}")
    except Exception as e:
        print(f"[startup] 建表失败（{type(e).__name__}: {e}），服务继续启动")

    try:
        from services import get_workspace_service
        ws = get_workspace_service().ensure_default()
        print(f"[startup] 默认工作区: {ws['name']} → {ws['path']}")
    except Exception as e:
        print(f"[startup] 默认工作区初始化失败（{type(e).__name__}: {e}），服务继续启动")

    try:
        from services import get_session_service
        get_session_service().reconcile_runs()  # 强杀后残留的「排队中」运行归档为已中断
    except Exception as e:
        print(f"[startup] 残留运行归档失败（{type(e).__name__}: {e}），服务继续启动")

    try:
        from services.broadcast import bind_loop
        bind_loop(asyncio.get_running_loop())
    except Exception:
        pass

    obs.bootstrap()   # 预置 0 值指标：抓取端在首个任务前也能确认服务存活

    print(f"[startup] {cfg.app_name} 就绪 · provider={cfg.provider} · model={cfg.model or '(未配置)'}")

    # 鉴权状态必须显式可见：开关开着却没配令牌属于配置错误（fail-closed），
    # 这类问题若只体现在"所有请求 401"上，排查成本极高。
    if cfg.auth_misconfigured:
        print("[startup] !! 严重配置错误：AUTH_ENABLED=true 但未配置 API_KEYS / AUTH_TOKEN，"
              "所有 API 请求都会被拒绝。请配置后重启。")
    elif cfg.auth_required:
        print(f"[startup] 鉴权已启用：{len(cfg.key_map)} 个令牌 · owner 数 "
              f"{len(set(cfg.key_map.values()))}")
    else:
        print("[startup] 鉴权未启用（AUTH_ENABLED=false）：仅适合本机单用户使用，"
              "对外暴露前请开启并配置 API_KEYS")
    yield
    print("[shutdown] 服务退出")


app = FastAPI(title=f"{cfg.app_name} API", version="1.0.0", lifespan=lifespan,
              description="求职业务域智能体平台后端（会话 / 工具 / 工作区 / 文件 / Git / Agent SSE）")

@app.middleware("http")
async def auth_middleware(request: Request, call_next):
    """全局鉴权（fail-closed）：/api/* 与 /metrics 默认受保护。

    为什么放在中间件而不是逐个路由加依赖：新增路由若忘记加依赖就会静默裸奔，
    而中间件是"默认拒绝、显式放行"，这个方向上的错误更安全。
    校验通过后把 owner 写进 request.state，业务路由用 `current_owner` 依赖读取。

    注册顺序（重要）：Starlette 的 `add_middleware` 后加入者位于外层，因此本中间件
    必须写在 `CORSMiddleware` **之前**——这样 CORS 才在最外层，401/403 响应同样带
    CORS 头（否则浏览器只看到网络错误、拿不到原因），预检 OPTIONS 也会被 CORS 先短路。
    """
    from security import authorize_request, is_public
    path = request.url.path
    if not is_public(path, cfg):
        try:
            request.state.owner = authorize_request(request)
        except HTTPException as exc:
            obs.inc("pathforge_auth_failures_total", path=path, status=exc.status_code)
            return JSONResponse({"detail": exc.detail}, status_code=exc.status_code,
                                headers=getattr(exc, "headers", None) or {})
        except Exception as e:  # noqa: BLE001 — 鉴权自身异常也必须拒绝，不能放行
            obs.inc("pathforge_auth_failures_total", path=path, status=500)
            return JSONResponse({"detail": f"鉴权失败: {type(e).__name__}"}, status_code=500)
    else:
        request.state.owner = getattr(request.state, "owner", "default")
    return await call_next(request)


# 必须在鉴权中间件之后注册（后加入者在外层），保证 401 响应带 CORS 头
app.add_middleware(
    CORSMiddleware,
    allow_origins=list(cfg.cors_origins or []),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/metrics", include_in_schema=False)
async def metrics() -> PlainTextResponse:
    """Prometheus 文本格式指标（受鉴权中间件保护）。"""
    from observability import render_prometheus
    return PlainTextResponse(render_prometheus(),
                             media_type="text/plain; version=0.0.4; charset=utf-8")


from api import router as api_router, ws as ws_router  # noqa: E402  (需在 cfg/app 之后)

app.include_router(api_router)
app.include_router(ws_router)


@app.exception_handler(NotFoundError)
async def _not_found_handler(request: Request, exc: NotFoundError) -> JSONResponse:
    """目标不存在（工作区/文件）→ 404；注意注册顺序要在 WorkspaceError 之前。"""
    return JSONResponse({"detail": str(exc)}, status_code=404)


@app.exception_handler(WorkspaceError)
async def _workspace_error_handler(request: Request, exc: WorkspaceError) -> JSONResponse:
    """路径越界/非法一律 400 —— 契约要求 `{"detail": "..."}` 形态。"""
    return JSONResponse({"detail": str(exc)}, status_code=400)


@app.exception_handler(ValueError)
async def _value_error_handler(request: Request, exc: ValueError) -> JSONResponse:
    """服务层的 ValueError 家族（参数非法）统一转 400，避免暴露为 500。"""
    return JSONResponse({"detail": str(exc)}, status_code=400)


# ---------------- 报告产物静态路由 ----------------
class ReportStaticFiles(StaticFiles):
    """只放行 `data/reports/` 下由本服务生成的报告与图表。

    为什么需要：报告正文里的图片/交互式 HTML 链接与 `final_answer.chart` 都是
    `/files/<相对项目根路径>` 形式（由 core/tools/report*.py 与提示词产出）。
    若把项目根目录整体挂成静态目录，`/files/.env` 可下载含密钥的配置、
    `/files/data/.key` 可下载加密密钥、`/files/data/sessions/*.json` 可下载全部会话存档——
    因此这里做**白名单前缀校验 + 路径归一化**，并剥离 `..` 与反斜杠绕过。
    更细粒度的按路径取用走 `/api/files/report`（同样白名单）。
    """

    ALLOW_PREFIX = "data/reports"

    async def get_response(self, path: str, scope):
        import posixpath
        norm = posixpath.normpath("/" + str(path).replace("\\", "/")).lstrip("/")
        if norm != self.ALLOW_PREFIX and not norm.startswith(self.ALLOW_PREFIX + "/"):
            return PlainTextResponse("Not Found", status_code=404)
        return await super().get_response(path, scope)


# 必须在 "/" 静态站点之前注册：Starlette 按注册顺序匹配路由
app.mount("/files", ReportStaticFiles(directory=str(PROJECT_ROOT)), name="report-files")


# ---------------- 静态站点（前端产物） ----------------
DIST_DIR = Path(PROJECT_ROOT) / "frontend" / "dist"
if DIST_DIR.is_dir():
    # html=True 让 / 与子目录回落到 index.html；API 前缀不在挂载点内，不会被吞
    app.mount("/", StaticFiles(directory=str(DIST_DIR), html=True), name="frontend")

    @app.get("/{full_path:path}", include_in_schema=False)
    async def spa_fallback(full_path: str) -> FileResponse:
        """SPA 回退：非 API/WS 路径未命中静态文件时交给前端路由处理。"""
        index = DIST_DIR / "index.html"
        candidate = DIST_DIR / full_path
        if full_path and candidate.is_file():
            return FileResponse(str(candidate))
        return FileResponse(str(index))
else:
    @app.get("/", include_in_schema=False)
    async def root_hint() -> dict:
        """未构建前端产物时的提示页（后端 API 仍完整可用）。"""
        return {"app": cfg.app_name, "api": "/api", "docs": "/docs",
                "hint": "前端产物未构建：请在 frontend/ 执行 npm install && npm run build"}


if __name__ == "__main__":  # pragma: no cover - 本地直跑用
    import uvicorn
    uvicorn.run(app, host=cfg.api_host, port=cfg.api_port, log_level="info")
