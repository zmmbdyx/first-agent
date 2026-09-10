"""工作区文件服务：文件树、内容预览、报告产物白名单读取。

两条安全底线：
1. 所有路径经 `WorkspaceService.resolve_path()`，越界即 WorkspaceError → 400；
2. 遍历不跟随符号链接（`is_symlink()` 直接跳过），避免链接成环或借链接读到工作区外的内容。

另外兼容既有加密落盘：材料/简历由 `core.secure_store` 以 `ENC1:` 前缀加密保存，
预览时按魔数判定后解密，否则前端只能看到乱码。
"""
from __future__ import annotations

from pathlib import Path
from typing import List, Optional, Tuple

from config import load_config, to_root_relative
from core import secure_store
from services.workspace_service import NotFoundError, WorkspaceError, WorkspaceService

# 单文件预览上限：够 Markdown/代码阅读，又不至于把响应体撑爆
MAX_PREVIEW_BYTES = 512 * 1024
# 报告白名单：只放行 data/reports/ 下的产物（.env、data/.key、会话存档绝不外泄）
REPORT_PREFIX = "data/reports"

IMAGE_EXT = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".svg", ".ico"}
PDF_EXT = {".pdf"}
MARKDOWN_EXT = {".md", ".markdown"}
TEXT_EXT = {".txt", ".json", ".yaml", ".yml", ".toml", ".ini", ".cfg", ".conf", ".env",
            ".log", ".csv", ".tsv", ".sql", ".sh", ".bat", ".ps1", ".py", ".js", ".jsx",
            ".ts", ".tsx", ".css", ".scss", ".html", ".htm", ".xml", ".vue", ".go", ".rs",
            ".java", ".c", ".h", ".cpp", ".rb", ".php", ".kt", ".swift"}
# 递归遍历时跳过的目录：体积大且与「工作区内容」无关
SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", "dist", "build",
             ".idea", ".vscode", ".mypy_cache", ".pytest_cache", ".ruff_cache"}
LANG_BY_EXT = {".py": "python", ".js": "javascript", ".jsx": "javascript", ".ts": "typescript",
               ".tsx": "typescript", ".json": "json", ".md": "markdown", ".markdown": "markdown",
               ".html": "html", ".htm": "html", ".css": "css", ".scss": "scss",
               ".yml": "yaml", ".yaml": "yaml", ".toml": "toml", ".sql": "sql",
               ".sh": "bash", ".bat": "bat", ".ps1": "powershell", ".go": "go",
               ".rs": "rust", ".java": "java", ".c": "c", ".h": "c", ".cpp": "cpp",
               ".rb": "ruby", ".php": "php", ".vue": "vue", ".xml": "xml", ".csv": "csv"}


def guess_type(path: Path) -> str:
    """按扩展名归类预览类型（text/markdown/image/pdf/binary）。"""
    ext = path.suffix.lower()
    if ext in IMAGE_EXT:
        return "image"
    if ext in PDF_EXT:
        return "pdf"
    if ext in MARKDOWN_EXT:
        return "markdown"
    if ext in TEXT_EXT:
        return "text"
    return "binary"


def guess_language(path: Path) -> str:
    return LANG_BY_EXT.get(path.suffix.lower(), "")


def read_bytes_any(path: Path) -> bytes:
    """读取文件字节：加密落盘的先解密（工具层与预览共用同一约定）。"""
    raw = path.read_bytes()
    if raw.startswith(secure_store.ENC_PREFIX):
        try:
            return secure_store.decrypt(raw)
        except Exception:
            return raw
    return raw


class FileService:
    """工作区文件访问（树 / 预览 / 原始字节 / 报告白名单）。"""

    def __init__(self, cfg=None, workspaces: Optional[WorkspaceService] = None) -> None:
        self.cfg = cfg or load_config()
        self.workspaces = workspaces or WorkspaceService(self.cfg)

    # ---------------- 路径 ----------------
    def workspace_dir(self, workspace: str) -> Path:
        return self.workspaces.resolve(workspace or "default")

    def resolve(self, workspace: str, rel: str) -> Path:
        """工作区内受限路径解析（越界抛 WorkspaceError）。"""
        return self.workspaces.resolve_path(workspace or "default", rel)

    # ---------------- 文件树 ----------------
    def tree(self, workspace: str = "default", path: str = "", depth: int = 3) -> dict:
        root = self.workspace_dir(workspace)
        base = self.resolve(workspace, path)
        if not base.exists():
            raise NotFoundError(f"目录不存在: {path or '.'}")
        if not base.is_dir():
            raise WorkspaceError(f"不是目录: {path}")
        max_depth = max(1, min(int(depth or 3), 8))
        return {"root": to_root_relative(base), "nodes": self._walk(base, root, max_depth)}

    def _walk(self, base: Path, ws_root: Path, depth: int) -> List[dict]:
        if depth <= 0:
            return []
        try:
            entries = sorted(base.iterdir(), key=lambda p: (p.is_file(), p.name.lower()))
        except OSError:
            return []
        nodes: List[dict] = []
        for p in entries:
            if p.name in SKIP_DIRS or p.is_symlink():  # 不跟链接：防止越界与遍历成环
                continue
            try:
                st = p.stat()
            except OSError:
                continue
            rel = self._rel(p, ws_root)
            if p.is_dir():
                nodes.append({"name": p.name, "path": rel, "type": "dir", "size": 0,
                              "mtime": st.st_mtime,
                              "children": self._walk(p, ws_root, depth - 1)})
            else:
                nodes.append({"name": p.name, "path": rel, "type": "file",
                              "size": st.st_size, "mtime": st.st_mtime, "children": []})
        return nodes

    @staticmethod
    def _rel(path: Path, ws_root: Path) -> str:
        try:
            return path.resolve().relative_to(ws_root.resolve()).as_posix()
        except ValueError:
            return path.name

    # ---------------- 内容预览 ----------------
    def content(self, workspace: str = "default", path: str = "") -> dict:
        target = self.resolve(workspace, path)
        if not target.exists() or not target.is_file():
            raise NotFoundError(f"文件不存在: {path}")
        kind = guess_type(target)
        size = target.stat().st_size
        if kind in ("image", "pdf", "binary"):
            # 二进制不给正文：前端用 /api/files/raw 拿原始字节
            return {"path": self._rel(target, self.workspace_dir(workspace)), "type": kind,
                    "content": "", "language": "", "size": size, "truncated": False}
        raw = read_bytes_any(target)[:MAX_PREVIEW_BYTES + 1]
        truncated = len(raw) > MAX_PREVIEW_BYTES
        if not truncated and b"\x00" in raw[:4096]:
            return {"path": self._rel(target, self.workspace_dir(workspace)), "type": "binary",
                    "content": "", "language": "", "size": size, "truncated": False}
        text = raw[:MAX_PREVIEW_BYTES].decode("utf-8", "replace")
        return {"path": self._rel(target, self.workspace_dir(workspace)), "type": kind,
                "content": text, "language": guess_language(target), "size": size,
                "truncated": truncated}

    def raw(self, workspace: str, path: str) -> Tuple[bytes, str]:
        """返回 (字节, media_type)；图片/PDF 预览走这里。"""
        target = self.resolve(workspace, path)
        if not target.exists() or not target.is_file():
            raise NotFoundError(f"文件不存在: {path}")
        return read_bytes_any(target), media_type_for(target)

    # ---------------- 报告产物白名单 ----------------
    def report_path(self, rel: str) -> Path:
        """`GET /api/files/report` 专用：只允许 data/reports/ 下的文件。

        为什么单独一条：项目根下还有 .env、data/.key、data/sessions/*.json 等敏感文件，
        旧版把整个根目录挂成静态目录导致可被直接下载；这里做前缀白名单并再次 resolve 校验。
        """
        raw = str(rel or "").strip().replace("\\", "/")
        if not raw:
            raise WorkspaceError("缺少报告路径参数 path")
        reports_dir = Path(self.cfg.reports_dir).resolve()
        if raw.startswith("/files/"):  # 前端可能直接把 /files/... 的返回值回传
            raw = raw[len("/files/"):]
        raw = raw.lstrip("/")
        if raw.startswith(REPORT_PREFIX):
            candidate = (Path(self.cfg.data_path).parent / raw).resolve()
        else:
            # 只取文件名：允许传 "报告.md" 或 "charts/x.png" 这类相对 reports 的路径
            candidate = (reports_dir / raw).resolve()
        try:
            candidate.relative_to(reports_dir)
        except ValueError:
            raise WorkspaceError("报告路径非法：仅允许 data/reports/ 下的产物")
        if not candidate.exists() or not candidate.is_file():
            raise NotFoundError(f"报告不存在: {raw}")
        return candidate


def media_type_for(path: Path) -> str:
    """原始字节响应的 Content-Type（图片/PDF 内联预览，其余按下载处理）。"""
    ext = path.suffix.lower()
    return {
        ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
        ".gif": "image/gif", ".webp": "image/webp", ".bmp": "image/bmp",
        ".svg": "image/svg+xml", ".ico": "image/x-icon", ".pdf": "application/pdf",
        ".json": "application/json; charset=utf-8",
        ".md": "text/markdown; charset=utf-8",
        ".html": "text/html; charset=utf-8", ".htm": "text/html; charset=utf-8",
        ".txt": "text/plain; charset=utf-8", ".csv": "text/csv; charset=utf-8",
    }.get(ext, "application/octet-stream")


__all__ = ["FileService", "guess_type", "guess_language", "media_type_for",
           "read_bytes_any", "MAX_PREVIEW_BYTES", "REPORT_PREFIX"]
