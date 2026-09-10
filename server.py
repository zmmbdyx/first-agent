"""FastAPI 服务：SSE 实时事件流（工作流可视化数据源）+ 简历库管理 + 截图OCR + 会话持久化。"""
import asyncio
import json
import re
import threading
import time
from pathlib import Path

from fastapi import FastAPI, File, UploadFile
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from config import load_config, ROOT
from core.agent import JobAgent

app = FastAPI(title="求职智囊 Agent")
cfg = load_config()

# 每会话独立 Agent 实例：EventBus/_session/缓存/统计均为实例态，
# 共享单例会在并发时互相覆盖事件归属（评估发现的 P0 并发缺陷）
_AGENTS: dict = {}
_AGENTS_LOCK = threading.Lock()
_AGENT_CAP = 50


def get_agent(session_id: str) -> JobAgent:
    with _AGENTS_LOCK:
        a = _AGENTS.get(session_id)
        if a is None:
            if len(_AGENTS) >= _AGENT_CAP:  # 容量护栏：清最旧的 1/3
                for sid in list(_AGENTS)[: _AGENT_CAP // 3]:  # dict保序=最先创建的先清
                    _AGENTS.pop(sid, None)
            a = JobAgent(cfg)
            _AGENTS[session_id] = a
        return a


_RUNNING: dict = {}   # session_id -> bool，防并发执行
_CURRENT_MODEL = {"name": cfg.model or (cfg.models[0] if cfg.models else "")}

# 共享工具箱：健康检查/OCR等无状态操作专用，不占用会话池容量
_shared_agent: JobAgent | None = None
_SHARED_AGENT_LOCK = threading.Lock()


def shared_agent() -> JobAgent:
    """懒加载共享 Agent。
    改动：原实现没有加锁，HTTP 线程池下首次并发请求会各自构造一个 JobAgent
    （重复加载工具箱/覆盖全局引用），这里用锁保证只构造一次。"""
    global _shared_agent
    if _shared_agent is None:
        with _SHARED_AGENT_LOCK:
            if _shared_agent is None:
                _shared_agent = JobAgent(cfg)
    return _shared_agent


class ReportStaticFiles(StaticFiles):
    """只允许通过 /files 访问 data/reports/ 下由本服务生成的报告与图表。
    改动：原实现把整个项目根目录挂成静态目录（StaticFiles(directory=ROOT_PATH)），
    实测 GET /files/.env 可下载含 LLM_API_KEY 的配置文件、GET /files/data/.key
    可下载会话/材料加密密钥、/files/data/sessions/*.json 可下载全部会话存档、
    /files/core/*.py 可下载源码——属于敏感信息直接外泄。改为白名单前缀校验，
    与前端约定一致（utils.js 只把 data/ 开头的路径映射到 /files/）。"""

    ALLOW_PREFIX = "data/reports"

    async def get_response(self, path: str, scope):
        import posixpath
        # 归一化并剥离 .. / 反斜杠，避免用 data/reports/../../.env 绕过白名单
        norm = posixpath.normpath("/" + str(path).replace("\\", "/")).lstrip("/")
        if norm != self.ALLOW_PREFIX and not norm.startswith(self.ALLOW_PREFIX + "/"):
            return PlainTextResponse("Not Found", status_code=404)
        return await super().get_response(path, scope)


ROOT_PATH = Path(ROOT)
RESUME_DIR = ROOT_PATH / "data" / "resumes"
UPLOAD_DIR = ROOT_PATH / "data" / "uploads"
RESUME_DIR.mkdir(parents=True, exist_ok=True)
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
ALLOWED_RESUME = {".pdf", ".docx", ".txt", ".md"}
ALLOWED_IMAGE = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}


def _sse(evt: dict) -> str:
    return f"data: {json.dumps(evt, ensure_ascii=False)}\n\n"


@app.get("/api/health")
def health():
    import importlib.util
    from core import cache as disk_cache
    from core import secure_store
    return {"provider": cfg.provider, "model": cfg.model,
            "base_url": cfg.base_url,
            "fallback_model": getattr(cfg, "fallback_model", "") or None,
            "tools": list(shared_agent().registry.tools.keys()),
            "tool_costs": {t.name: t.cost for t in shared_agent().registry.tools.values()},
            "ocr_ready": importlib.util.find_spec("rapidocr_onnxruntime") is not None,
            "encrypted_storage": secure_store.enabled(),
            "cache": disk_cache.stats()}


def _fix_multipart_name(name: str) -> str:
    """兼容 Windows 命令行/旧客户端按 GBK 发送的文件名（浏览器 fetch 走 UTF-8 不受影响）。"""
    if not name:
        return name
    try:
        fixed = name.encode("latin-1").decode("gbk")
        if fixed != name and any("\u4e00" <= ch <= "\u9fff" for ch in fixed):
            return fixed
    except (UnicodeEncodeError, UnicodeDecodeError):
        pass
    return name


# ---------------- 简历库管理 ----------------
@app.get("/api/resumes")
def list_resumes():
    items = []
    if RESUME_DIR.exists():
        for p in RESUME_DIR.iterdir():
            if p.is_file() and p.suffix.lower() in ALLOWED_RESUME:
                st = p.stat()
                items.append({"name": p.name, "size": st.st_size, "mtime": st.st_mtime,
                              "path": f"data/resumes/{p.name}"})
    items.sort(key=lambda x: -x["mtime"])
    return {"resumes": items}


@app.post("/api/resumes/upload")
async def upload_resume(file: UploadFile = File(...)):
    raw = Path(_fix_multipart_name(file.filename or "")).name
    ext = Path(raw).suffix.lower()
    if ext not in ALLOWED_RESUME:
        return JSONResponse({"error": f"仅支持 {'/'.join(sorted(ALLOWED_RESUME))}，收到: {raw}"}, status_code=400)
    data = await file.read()
    if len(data) > 10 * 1024 * 1024:
        return JSONResponse({"error": "文件超过10MB"}, status_code=400)
    safe = re.sub(r"[^\w\u4e00-\u9fff.\-]", "_", raw) or f"resume{ext}"
    path = RESUME_DIR / safe
    if path.exists():  # 重名加时间戳，避免覆盖
        path = RESUME_DIR / f"{Path(safe).stem}_{time.strftime('%H%M%S')}{ext}"
    from core import secure_store
    secure_store.write_bytes(path, data)  # 简历落盘即加密
    return {"ok": True, "name": path.name, "path": f"data/resumes/{path.name}", "size": len(data)}


@app.delete("/api/resumes/{name}")
def delete_resume(name: str):
    safe = Path(name).name  # 防目录穿越
    p = RESUME_DIR / safe
    if not p.exists() or p.suffix.lower() not in ALLOWED_RESUME:
        return JSONResponse({"error": "文件不存在"}, status_code=404)
    p.unlink()
    return {"ok": True}


# ---------------- 截图OCR ----------------
@app.post("/api/ocr")
async def ocr_image(file: UploadFile = File(...)):
    raw = Path(_fix_multipart_name(file.filename or "")).name or "paste.png"
    ext = Path(raw).suffix.lower() or ".png"
    if ext not in ALLOWED_IMAGE:
        return JSONResponse({"error": f"仅支持图片 {'/'.join(sorted(ALLOWED_IMAGE))}"}, status_code=400)
    data = await file.read()
    if len(data) > 15 * 1024 * 1024:
        return JSONResponse({"error": "图片超过15MB"}, status_code=400)
    save = UPLOAD_DIR / f"jd_{time.strftime('%Y%m%d_%H%M%S')}{ext}"
    from core import secure_store
    secure_store.write_bytes(save, data)  # 截图落盘即加密
    result = shared_agent().registry.call("image_ocr", {"path": str(save)})
    if not result.ok:
        return JSONResponse({"error": result.error}, status_code=422)
    return {"ok": True, "text": result.data["text"], "chars": result.data["chars"],
            "image": f"data/uploads/{save.name}"}


# ---------------- 通用材料上传（JD文档等，非简历库） ----------------
@app.post("/api/upload")
async def upload_material(file: UploadFile = File(...)):
    raw = Path(_fix_multipart_name(file.filename or "")).name
    ext = Path(raw).suffix.lower()
    allowed = ALLOWED_RESUME | ALLOWED_IMAGE
    if ext not in allowed:
        return JSONResponse({"error": f"仅支持 {'/'.join(sorted(allowed))}"}, status_code=400)
    data = await file.read()
    if len(data) > 15 * 1024 * 1024:
        return JSONResponse({"error": "文件超过15MB"}, status_code=400)
    safe = re.sub(r"[^\w\u4e00-\u9fff.\-]", "_", raw) or f"file{ext}"
    save = UPLOAD_DIR / f"{time.strftime('%H%M%S')}_{safe}"
    from core import secure_store
    secure_store.write_bytes(save, data)  # 材料落盘即加密
    return {"ok": True, "path": f"data/uploads/{save.name}", "name": save.name}


class ModelReq(BaseModel):
    model: str


@app.get("/api/models")
def list_models():
    """可用模型列表与当前选中（多模型切换）。"""
    models = cfg.models or ([cfg.model] if cfg.model else [])
    current = _CURRENT_MODEL["name"]
    return {"models": models, "current": current, "provider": cfg.provider}


@app.post("/api/model/select")
def select_model(req: ModelReq):
    if req.model not in (cfg.models or []):
        return JSONResponse({"error": f"未配置的模型: {req.model}，可用: {cfg.models}"}, status_code=400)
    _CURRENT_MODEL["name"] = req.model  # 全局生效
    for a in _AGENTS.values():          # 已存在实例同步（降级中的实例不覆盖）
        if not getattr(a.llm, "degraded", False):
            a.llm.set_model(req.model)
    return {"ok": True, "current": req.model}


class ChatReq(BaseModel):
    session_id: str = ""
    message: str = Field(..., max_length=32000)   # 防超长输入打爆 Token/DoS
    resume_path: str = ""   # 简历库中选中的简历，随消息生效


@app.post("/api/chat")
async def chat(req: ChatReq):
    ag = get_agent(req.session_id or "adhoc")
    memory = ag.memory
    session = memory.load(req.session_id) if req.session_id else memory.new_session()
    if session is None:
        session = memory.new_session()
    if _CURRENT_MODEL["name"] and not getattr(ag.llm, "degraded", False):
        ag.llm.set_model(_CURRENT_MODEL["name"])  # 每次会话同步全局模型选择
    if _RUNNING.get(session.id):
        return JSONResponse({"error": "该会话正在执行中，请稍候"}, status_code=409)
    if req.resume_path:  # 用户选择的简历优先：插入 facts 首位
        name = Path(req.resume_path).name
        old = [p for p in (session.facts.get("resume_paths") or []) if Path(p).name != name]
        session.facts["resume_paths"] = [f"data/resumes/{name}"] + old

    loop = asyncio.get_running_loop()
    q: asyncio.Queue = asyncio.Queue()
    unsubscribe = ag.bus.subscribe(lambda evt: loop.call_soon_threadsafe(q.put_nowait, evt))

    def run():
        try:
            ag.handle_message(session, req.message)
        finally:
            _RUNNING[session.id] = False
            loop.call_soon_threadsafe(q.put_nowait, {"type": "__end__"})

    _RUNNING[session.id] = True
    threading.Thread(target=run, daemon=True).start()

    async def gen():
        yield _sse({"type": "session_info", "session_id": session.id,
                    "status": session.status, "title": session.title})
        try:
            while True:
                evt = await q.get()
                if evt.get("type") == "__end__":
                    break
                if evt.get("session_id") not in (None, session.id):
                    continue  # 过滤其他会话的事件
                yield _sse(evt)
            snapshot = session.to_dict()
            yield _sse({"type": "session_snapshot",
                        "tasks": snapshot["tasks"], "messages": snapshot["messages"][-20:],
                        "status": session.status, "facts": snapshot["facts"],
                        "pending_question": session.pending_question})
        finally:
            unsubscribe()

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.get("/api/session/{sid}")
def get_session(sid: str):
    # 改动：原实现调用未定义的全局 memory，实测 GET /api/session/xxx 直接 NameError→500；
    # 改为通过共享 Agent 的 Memory 实例读取（与会话池同一存储目录）。
    s = shared_agent().memory.load(sid)
    if not s:
        return JSONResponse({"error": "会话不存在"}, status_code=404)
    return s.to_dict()


@app.delete("/api/session/{sid}")
def delete_session(sid: str):
    """清空聊天记录：删除该会话的服务端存档（加密文件一并移除）。"""
    safe = Path(sid).name
    if not re.fullmatch(r"[0-9a-f]{6,16}", safe):
        return JSONResponse({"error": "会话ID不合法"}, status_code=400)
    p = shared_agent().memory.dir / f"{safe}.json"
    if not p.exists():
        return JSONResponse({"error": "会话不存在"}, status_code=404)
    p.unlink()
    return {"ok": True}


@app.get("/")
def index():
    return FileResponse(ROOT_PATH / "static" / "index.html")


app.mount("/static", StaticFiles(directory=ROOT_PATH / "static"), name="static")
# 改动：不再把项目根目录整体暴露为静态目录（见 ReportStaticFiles 的说明），
# 仅放行 data/reports/ 下的生成物（图表、交互式HTML、报告正文）。
app.mount("/files", ReportStaticFiles(directory=ROOT_PATH), name="files")


if __name__ == "__main__":
    import uvicorn
    print(f"AI求职助手启动中: http://127.0.0.1:8000  (LLM provider = {cfg.provider}/{cfg.model})")
    uvicorn.run(app, host="127.0.0.1", port=8000, log_level="warning")
