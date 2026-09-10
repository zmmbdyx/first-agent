"""工作区服务：工作区登记 + 路径沙箱。

安全模型（被 files / git 路由共用）：
1. 任何 `path` 先与工作区根拼接，再 `resolve()`，最后必须仍位于工作区根之下——
   这样 `..`、绝对路径、以及指向外部的符号链接会同时被挡住（resolve 会展开链接）；
2. 目标不存在时，向上找到最近的存在祖先做同样的越界判定，堵住「先写后逃逸」的路径；
3. 工作区目录本身也必须落在配置的工作区根（`WORKSPACE_ROOT`）内，避免把工作区
   登记成 `C:\\` 这类整盘根目录。
"""
from __future__ import annotations

from pathlib import Path
from typing import List, Optional

from sqlalchemy import select

from config import load_config
from models import Workspace
from models.base import gen_id, iso, utcnow
from db import SessionLocal

# 越界/非法路径统一抛它，路由层转 400
PATH_ERROR_TIP = "路径非法：必须是工作区内的相对路径，且不得包含 .. 或指向工作区外"


class WorkspaceError(ValueError):
    """工作区相关错误（路径越界 / 名称冲突 / 根目录非法）→ 路由层转 400。"""


class NotFoundError(WorkspaceError):
    """资源不存在（工作区/文件）→ 路由层转 404。

    与 WorkspaceError 分开是为了让错误码语义正确：越权访问是 400，
    目标不存在是 404，前端据此决定「提示」还是「引导创建」。
    """


def _posix(path: str) -> str:
    """路径统一用正斜杠出参：前端在 Windows/macOS 上都能直接做字符串拼接。"""
    return str(path or "").replace("\\", "/")


def _within(path: Path, root: Path) -> bool:
    """path 是否等于 root 或位于 root 之内（两侧都应为 resolve() 后的绝对路径）。"""
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _safe_existing_target(target: Path, root: Path) -> None:
    """符号链接逃逸校验：即使目标不存在，也要保证其最近存在祖先不越界。"""
    probe = target
    while not probe.exists() and probe != probe.parent:
        probe = probe.parent
    if not _within(probe.resolve(), root.resolve()):
        raise WorkspaceError(PATH_ERROR_TIP)


def safe_join(root: Path, rel: str) -> Path:
    """把受限相对路径拼到工作区根上；越界、绝对路径、符号链接逃逸一律拒绝。"""
    raw = str(rel or "").strip().replace("\\", "/")
    if raw in ("", "."):
        target = root.resolve()
        _safe_existing_target(target, root)
        return target
    if raw.startswith("/") or Path(raw).is_absolute() or ":" in raw.split("/")[0]:
        raise WorkspaceError(PATH_ERROR_TIP)
    root_r = root.resolve()
    target = (root_r / raw)
    resolved = target.resolve()  # 展开符号链接与 ..
    if not _within(resolved, root_r):
        raise WorkspaceError(PATH_ERROR_TIP)
    _safe_existing_target(target, root_r)
    return resolved


class WorkspaceService:
    """工作区 CRUD 与路径解析（`resolve()` 是 files/git 路由的唯一入口）。"""

    def __init__(self, cfg=None) -> None:
        self.cfg = cfg or load_config()

    # ---------------- 基础路径 ----------------
    @property
    def root(self) -> Path:
        """工作区根目录（WORKSPACE_ROOT，相对路径按项目根解析）。"""
        root = Path(self.cfg.workspace_path)
        root.mkdir(parents=True, exist_ok=True)
        return root.resolve()

    def _abs(self, path: str) -> Path:
        """配置里的相对路径按项目根解析，与 config.to_root_relative 口径一致。"""
        p = Path(str(path or "")).expanduser()
        return p if p.is_absolute() else (Path(self.cfg.data_path).parent / p)

    # ---------------- CRUD ----------------
    def create(self, name: str, path: str = "", description: str = "", ident: str = "",
               db=None) -> dict:
        own = db is None
        db = db or SessionLocal()
        try:
            name = (name or "").strip()
            if not name:
                raise WorkspaceError("工作区名称不能为空")
            if db.scalars(select(Workspace).where(Workspace.name == name)).first():
                raise WorkspaceError(f"工作区名称已存在: {name}")
            raw = (path or "").strip() or str(Path("workspaces") / name)
            target = self._abs(raw)
            root = self.root
            if not _within(target.resolve(), root):
                raise WorkspaceError(f"工作区目录必须位于 {root} 之内")
            target.mkdir(parents=True, exist_ok=True)
            row = Workspace(id=ident or gen_id(), name=name, path=raw,
                            description=description or "", created_at=utcnow(),
                            updated_at=utcnow())
            db.add(row)
            db.commit()
            return self._out(row)
        finally:
            if own:
                db.close()

    def list(self, db=None) -> List[dict]:
        own = db is None
        db = db or SessionLocal()
        try:
            rows = db.scalars(select(Workspace).order_by(Workspace.created_at.asc())).all()
            return [self._out(r) for r in rows]
        finally:
            if own:
                db.close()

    def get(self, ident: str, db=None) -> Optional[dict]:
        row = self._row(ident, db)
        return self._out(row) if row else None

    def update(self, ident: str, db=None, **fields) -> Optional[dict]:
        own = db is None
        db = db or SessionLocal()
        try:
            row = self._row(ident, db)
            if row is None:
                return None
            name = (fields.get("name") or "").strip()
            if name and name != row.name:
                dup = db.scalars(select(Workspace).where(Workspace.name == name)).first()
                if dup is not None:
                    raise WorkspaceError(f"工作区名称已存在: {name}")
                row.name = name
            new_path = (fields.get("path") or "").strip()
            if new_path and new_path != row.path:
                target = self._abs(new_path)
                if not _within(target.resolve(), self.root):
                    raise WorkspaceError(f"工作区目录必须位于 {self.root} 之内")
                target.mkdir(parents=True, exist_ok=True)
                row.path = new_path
            if fields.get("description") is not None:
                row.description = fields["description"]
            row.updated_at = utcnow()
            db.commit()
            return self._out(row)
        finally:
            if own:
                db.close()

    def delete(self, ident: str, db=None) -> bool:
        own = db is None
        db = db or SessionLocal()
        try:
            row = self._row(ident, db)
            if row is None:
                return False
            if row.name == "default":
                raise WorkspaceError("默认工作区不可删除")
            db.delete(row)
            db.commit()
            return True
        finally:
            if own:
                db.close()

    def ensure_default(self) -> dict:
        """启动时保证存在名为 default 的默认工作区（幂等）。"""
        with SessionLocal() as db:
            row = db.scalars(select(Workspace).where(Workspace.name == "default")).first()
            if row is not None:
                return self._out(row)
        return self.create(name="default", path=str(Path("workspaces") / "default"),
                           description="默认工作区（首次启动自动创建）", ident="default")

    # ---------------- 路径解析 ----------------
    def resolve(self, ident: str) -> Path:
        """id 或 name → 已存在的工作区目录绝对路径；不在工作区根内则报错。"""
        row = self._row(ident or "default", None)
        if row is None:
            raise NotFoundError(f"工作区不存在: {ident or 'default'}")
        target = self._abs(row.path).resolve()
        root = self.root
        if not _within(target, root):
            raise WorkspaceError(f"工作区目录越界（应在 {root} 内）: {target}")
        target.mkdir(parents=True, exist_ok=True)
        return target

    def resolve_path(self, ident: str, rel: str) -> Path:
        """工作区内的受限路径（files/git 路由统一走这里）。"""
        return safe_join(self.resolve(ident), rel)

    # ---------------- 内部 ----------------
    def _row(self, ident: str, db) -> Optional[Workspace]:
        """取工作区行（id 优先，其次 name；都取不到时退回最早创建的一个）。"""
        own = db is None
        db = db or SessionLocal()
        try:
            key = (ident or "").strip()
            if not key:
                return None
            row = db.get(Workspace, key)
            if row is None:
                row = db.scalars(select(Workspace).where(Workspace.name == key)).first()
            if row is None and key in ("default", "默认工作区"):
                row = db.scalars(select(Workspace).order_by(Workspace.created_at.asc())).first()
            return row
        finally:
            if own:
                db.close()

    def _row_fields(self, ident: str) -> Optional[dict]:
        """把行字段读成普通 dict 后再关闭会话，避免游离实例触发隐式刷新。"""
        own_db = SessionLocal()
        try:
            row = self._row(ident, own_db)
            if row is None:
                return None
            return {"id": row.id, "name": row.name, "path": _posix(row.path),
                    "description": row.description or "",
                    "created_at": iso(row.created_at), "updated_at": iso(row.updated_at)}
        finally:
            own_db.close()

    def _out(self, row: Workspace) -> dict:
        """行 → WorkspaceOut（exists/file_count 需实地统计，故不放在 SQL 里做）。"""
        return self._decorate({"id": row.id, "name": row.name, "path": _posix(row.path),
                               "description": row.description or "",
                               "created_at": iso(row.created_at),
                               "updated_at": iso(row.updated_at)})

    def _decorate(self, data: dict) -> dict:
        """补 exists/file_count：实地 stat 一次，顺带校验目录是否真的落在工作区根内。"""
        data["path"] = _posix(data["path"])
        target = self._abs(data["path"])
        exists = target.exists()
        count = 0
        if exists:
            try:
                count = sum(1 for p in target.rglob("*") if p.is_file())
            except OSError:
                count = 0
        return {**data, "exists": exists, "file_count": count}


__all__ = ["WorkspaceService", "WorkspaceError", "NotFoundError", "safe_join",
           "PATH_ERROR_TIP"]
