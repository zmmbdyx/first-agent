"""业务资产路由：简历库 / 通用材料上传 / 截图 OCR / 模型列表与切换 / 健康检查。

这些能力在重构前就存在（见 legacy/server.py），属于求职业务域必需项，因此按新布局
原样保留：文件仍落在 `data/resumes`、`data/uploads`，仍经 `core.secure_store` 加密落盘，
只有读取路径改走服务层与统一错误码。
"""
from __future__ import annotations

import importlib.util
import re
import socket
import threading
import time
from pathlib import Path
from typing import Any, Dict, List
from urllib.parse import urlparse

from fastapi import APIRouter, File, HTTPException, UploadFile

from config import ROOT, load_config
from core import secure_store
from schemas.file import ModelSelectIn, ModelsOut
from schemas.common import ItemsOut

router = APIRouter(tags=["assets"])
health_router = APIRouter(tags=["health"])

cfg = load_config()
RESUME_DIR = Path(ROOT) / "data" / "resumes"
UPLOAD_DIR = Path(ROOT) / "data" / "uploads"
ALLOWED_RESUME = {".pdf", ".docx", ".txt", ".md"}
ALLOWED_IMAGE = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}
MAX_RESUME_BYTES = 10 * 1024 * 1024
MAX_UPLOAD_BYTES = 15 * 1024 * 1024

# 当前选中模型：进程级状态（单实例部署），与旧版行为一致
_model_lock = threading.Lock()
_current_model = {"name": cfg.model or (cfg.models[0] if cfg.models else "")}


def _fix_multipart_name(name: str) -> str:
    """兼容按 GBK 发送文件名的旧客户端（浏览器 fetch 走 UTF-8 不受影响）。"""
    if not name:
        return name
    try:
        fixed = name.encode("latin-1").decode("gbk")
        if fixed != name and any("\u4e00" <= ch <= "\u9fff" for ch in fixed):
            return fixed
    except (UnicodeEncodeError, UnicodeDecodeError):
        pass
    return name


def _safe_name(raw: str, fallback: str) -> str:
    """只保留文件名并替换特殊字符：杜绝 `../` 与盘符注入。"""
    name = Path(_fix_multipart_name(raw or "")).name
    return re.sub(r"[^\w\u4e00-\u9fff.\-]", "_", name) or fallback


# ---------------- 简历库 ----------------
@router.get("/resumes", response_model=ItemsOut)
def list_resumes() -> dict:
    items: List[dict] = []
    if RESUME_DIR.exists():
        for p in RESUME_DIR.iterdir():
            if p.is_file() and p.suffix.lower() in ALLOWED_RESUME:
                st = p.stat()
                items.append({"name": p.name, "size": st.st_size, "mtime": st.st_mtime,
                              "path": f"data/resumes/{p.name}"})
    items.sort(key=lambda x: -x["mtime"])
    return {"items": items}


@router.post("/resumes/upload")
async def upload_resume(file: UploadFile = File(...)) -> dict:
    raw = Path(_fix_multipart_name(file.filename or "")).name
    ext = Path(raw).suffix.lower()
    if ext not in ALLOWED_RESUME:
        raise HTTPException(status_code=400,
                            detail=f"仅支持 {'/'.join(sorted(ALLOWED_RESUME))}，收到: {raw}")
    data = await file.read()
    if len(data) > MAX_RESUME_BYTES:
        raise HTTPException(status_code=400, detail="文件超过10MB")
    safe = _safe_name(raw, f"resume{ext}")
    path = RESUME_DIR / safe
    if path.exists():  # 重名加时间戳，避免覆盖用户已有简历
        path = RESUME_DIR / f"{Path(safe).stem}_{time.strftime('%H%M%S')}{ext}"
    RESUME_DIR.mkdir(parents=True, exist_ok=True)
    secure_store.write_bytes(path, data)
    return {"ok": True, "name": path.name, "path": f"data/resumes/{path.name}",
            "size": len(data)}


@router.delete("/resumes/{name}")
def delete_resume(name: str) -> dict:
    safe = Path(name).name  # 防目录穿越
    p = RESUME_DIR / safe
    if not p.exists() or p.suffix.lower() not in ALLOWED_RESUME:
        raise HTTPException(status_code=404, detail="文件不存在")
    p.unlink()
    return {"ok": True}


# ---------------- 通用材料 / 截图 OCR ----------------
@router.post("/upload")
async def upload_material(file: UploadFile = File(...)) -> dict:
    raw = Path(_fix_multipart_name(file.filename or "")).name
    ext = Path(raw).suffix.lower()
    allowed = ALLOWED_RESUME | ALLOWED_IMAGE
    if ext not in allowed:
        raise HTTPException(status_code=400, detail=f"仅支持 {'/'.join(sorted(allowed))}")
    data = await file.read()
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=400, detail="文件超过15MB")
    safe = _safe_name(raw, f"file{ext}")
    save = UPLOAD_DIR / f"{time.strftime('%H%M%S')}_{safe}"
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    secure_store.write_bytes(save, data)
    return {"ok": True, "path": f"data/uploads/{save.name}", "name": save.name,
            "size": len(data)}


@router.post("/ocr")
async def ocr_image(file: UploadFile = File(...)) -> dict:
    raw = Path(_fix_multipart_name(file.filename or "")).name or "paste.png"
    ext = Path(raw).suffix.lower() or ".png"
    if ext not in ALLOWED_IMAGE:
        raise HTTPException(status_code=400, detail=f"仅支持图片 {'/'.join(sorted(ALLOWED_IMAGE))}")
    data = await file.read()
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=400, detail="图片超过15MB")
    save = UPLOAD_DIR / f"jd_{time.strftime('%Y%m%d_%H%M%S')}{ext}"
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    secure_store.write_bytes(save, data)  # 截图落盘即加密
    from services import get_tool_service
    result = get_tool_service().registry().call("image_ocr", {"path": str(save)})
    if not result.ok:
        raise HTTPException(status_code=422, detail=result.error)
    return {"ok": True, "text": result.data.get("text", ""),
            "chars": result.data.get("chars", 0),
            "image": f"data/uploads/{save.name}"}


# ---------------- 模型 ----------------
@router.get("/models", response_model=ModelsOut)
def list_models() -> dict:
    """可用模型列表与当前选中（多模型切换）。凭据/端点全部来自环境变量。

    mock（未配置凭据）时给一个中性占位名，前端下拉框才不会空白；
    真实环境下这里就是 LLM_MODELS 配置的清单。
    """
    models = list(cfg.models or ([cfg.model] if cfg.model else []))
    if not models:
        models = ["mock"]
    return {"models": models, "current": _current_model["name"] or models[0],
            "provider": cfg.provider}


@router.post("/model/select")
def select_model(payload: ModelSelectIn) -> dict:
    if payload.model not in (cfg.models or []):
        raise HTTPException(status_code=400,
                            detail=f"未配置的模型: {payload.model}，可用: {list(cfg.models or [])}")
    with _model_lock:
        _current_model["name"] = payload.model
    try:  # 已存在的工具箱实例同步切换（降级中的实例不覆盖，与旧版一致）
        from services import get_tool_service
        llm = get_tool_service().agent.llm
        if not getattr(llm, "degraded", False):
            llm.set_model(payload.model)
    except Exception:
        pass
    return {"ok": True, "current": payload.model}


# ---------------- 健康检查 ----------------
def _probe_redis(url: str) -> dict:
    """Redis 可用性探测：只做一次 TCP 连接，不引入 redis 依赖、不阻塞启动。"""
    if not url:
        return {"configured": False, "available": False, "mode": "in-memory"}
    try:
        parsed = urlparse(url if "://" in url else f"redis://{url}")
        host = parsed.hostname or "127.0.0.1"
        port = parsed.port or 6379
        with socket.create_connection((host, port), timeout=0.4):
            return {"configured": True, "available": True, "mode": "redis"}
    except OSError:
        return {"configured": True, "available": False, "mode": "in-memory"}


def _probe_vector_store(url: str, path: str) -> dict:
    """向量库：远端不可达即降级为本地文件（契约要求，故 health 里如实标注 mode）。"""
    if url:
        try:
            import httpx
            with httpx.Client(timeout=0.5) as client:
                resp = client.get(url)
                if resp.status_code < 500:
                    return {"configured": True, "available": True, "mode": "remote"}
        except Exception:
            pass
        return {"configured": True, "available": False, "mode": "local-file",
                "path": path}
    return {"configured": False, "available": bool(path), "mode": "local-file",
            "path": path}


@health_router.get("/health")
def health() -> Dict[str, Any]:
    """`GET /api/health`：契约字段一个不少，附加信息只增不改。"""
    result: Dict[str, Any] = {"status": "ok", "provider": cfg.provider, "model": cfg.model}
    try:
        from core import cache as disk_cache
        result["cache"] = dict(disk_cache.stats())
    except Exception as e:
        result["cache"] = {"error": f"{type(e).__name__}: {e}"}
    try:
        from db import describe, ping
        result["db"] = {**describe(), "ok": ping()}
    except Exception as e:
        result["db"] = {"ok": False, "error": f"{type(e).__name__}: {e}",
                        "dialect": "unknown", "fallback": False}
    try:
        from services import get_tool_service
        registry = get_tool_service().registry()
        result["tools"] = list(registry.tools.keys())
        result["tool_costs"] = {t.name: t.cost for t in registry.tools.values()}
        result["tool_count"] = len(registry.tools)
    except Exception as e:
        result["tools"] = []
        result["tool_count"] = 0
        result["tools_error"] = f"{type(e).__name__}: {e}"
    result["redis"] = _probe_redis(getattr(cfg, "redis_url", ""))
    result["vector_store"] = _probe_vector_store(getattr(cfg, "vector_store_url", ""),
                                                 getattr(cfg, "vector_store_path", ""))
    result["sandbox"] = {
        "enabled": bool(cfg.sandbox_enabled), "timeout": cfg.sandbox_timeout,
        "max_output": cfg.sandbox_max_output,
        "permission_modes": ["read_only", "workspace_write", "full_access"],
        "full_access_allowed": bool(cfg.allow_full_access),
    }
    result["encrypted_storage"] = secure_store.enabled()
    result["ocr_ready"] = importlib.util.find_spec("rapidocr_onnxruntime") is not None
    result["llm_ready"] = bool(getattr(cfg, "llm_ready", cfg.provider != "mock"))
    return result


@health_router.get("/config")
def public_config() -> dict:
    """前端设置面板需要的**非敏感**配置快照（绝不回传 api_key / 连接串）。"""
    return {"app_name": cfg.app_name, "app_env": cfg.app_env, "provider": cfg.provider,
            "model": cfg.model, "models": list(cfg.models or []),
            "temperature": cfg.temperature, "max_react_steps": cfg.max_react_steps,
            "max_tasks": cfg.max_tasks, "privacy_mode": bool(cfg.privacy_mode),
            "workspace_root": str(cfg.workspace_path), "sandbox_enabled": bool(cfg.sandbox_enabled),
            "allow_full_access": bool(cfg.allow_full_access),
            "ocr_ready": importlib.util.find_spec("rapidocr_onnxruntime") is not None}


__all__ = ["router", "health_router"]
