"""工作区 Git 服务：变更列表 / stage / unstage。

要点：
- 一律用参数列表调用 git（`shell=False`），路径参数再经工作区沙箱解析，杜绝命令注入；
- 未安装 git、目录不是仓库、仓库无提交（unborn HEAD）都视为**正常业务状态**返回空结果，
  不抛异常——右侧 Git 面板需要的是「is_repo=false」而不是 500。
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import List, Optional

from config import load_config
from services.workspace_service import WorkspaceError, WorkspaceService

# git 命令超时（秒）：工作区可能很大，但绝不允许卡死请求线程
GIT_TIMEOUT = 20
# 变更列表上限：避免超大仓库把响应撑爆
MAX_CHANGES = 500


class GitUnavailable(Exception):
    """git 可执行文件缺失。"""


class GitService:
    """工作区 Git 操作。"""

    def __init__(self, cfg=None, workspaces: Optional[WorkspaceService] = None) -> None:
        self.cfg = cfg or load_config()
        self.workspaces = workspaces or WorkspaceService(self.cfg)

    # ---------------- 基础 ----------------
    @staticmethod
    def available() -> bool:
        return shutil.which("git") is not None

    def _run(self, cwd: Path, *args: str) -> tuple[int, str, str]:
        if not self.available():
            raise GitUnavailable("未检测到 git 可执行文件")
        try:
            proc = subprocess.run(["git", *args], cwd=str(cwd), capture_output=True,
                                  text=True, encoding="utf-8", errors="replace",
                                  timeout=GIT_TIMEOUT, shell=False)
        except subprocess.TimeoutExpired:
            return 124, "", f"git {' '.join(args)} 超时（>{GIT_TIMEOUT}s）"
        except OSError as e:
            raise GitUnavailable(f"git 调用失败: {e}")
        return proc.returncode, proc.stdout or "", proc.stderr or ""

    def is_repo(self, root: Path) -> bool:
        if not self.available():
            return False
        code, out, _ = self._run(root, "rev-parse", "--is-inside-work-tree")
        return code == 0 and out.strip().lower().startswith("true")

    # ---------------- 查询 ----------------
    def status(self, workspace: str = "default") -> dict:
        root = self.workspaces.resolve(workspace or "default")
        if not self.is_repo(root):
            return {"is_repo": False, "branch": "", "changes": []}
        code, out, _ = self._run(root, "status", "--porcelain=v1", "--branch",
                                 "-uall", "--no-renames")
        branch, changes = "", []
        for line in out.splitlines():
            if line.startswith("## "):
                branch = self._branch_of(line[3:])
                continue
            if len(line) < 4:
                continue
            xy, path = line[:2], line[3:].strip().strip('"')
            changes.append({"path": path.replace("\\", "/"),
                            "status": (xy[1] if xy[1] != " " else xy[0]),
                            "staged": xy[0] not in (" ", "?")})
            if len(changes) >= MAX_CHANGES:
                break
        return {"is_repo": True, "branch": branch, "changes": changes}

    @staticmethod
    def _branch_of(header: str) -> str:
        name = header.split("...")[0].strip()
        return name or ""

    # ---------------- 变更操作 ----------------
    def stage(self, workspace: str, paths: List[str]) -> dict:
        root = self._workspace_repo(workspace)
        targets = self._safe_paths(workspace, root, paths)
        if targets:
            code, _, err = self._run(root, "add", "--", *targets)
            if code != 0:
                raise WorkspaceError(f"git add 失败: {err.strip()}")
        return self.status(workspace)

    def unstage(self, workspace: str, paths: List[str]) -> dict:
        root = self._workspace_repo(workspace)
        targets = self._safe_paths(workspace, root, paths)
        if targets:
            # unborn HEAD 没有 HEAD 可 reset，退回 git rm --cached
            code, _, err = self._run(root, "reset", "-q", "HEAD", "--", *targets)
            if code != 0:
                code, _, err = self._run(root, "rm", "--cached", "-r", "-q", "--", *targets)
                if code != 0:
                    raise WorkspaceError(f"git unstage 失败: {err.strip()}")
        return self.status(workspace)

    # ---------------- 内部 ----------------
    def _workspace_repo(self, workspace: str) -> Path:
        root = self.workspaces.resolve(workspace or "default")
        if not self.is_repo(root):
            raise WorkspaceError("当前工作区不是 Git 仓库（可在工作区内执行 git init）")
        return root

    def _safe_paths(self, workspace: str, root: Path, paths: List[str]) -> List[str]:
        """把入参路径逐个过沙箱，转换成相对仓库根的路径；越界即拒绝整次请求。

        同时校验存在性：`git add` 对不存在的 pathspec 会整条命令失败（连已存在的
        文件也不会入库），与其把 git 的英文报错抛给前端，不如提前给出中文清单。
        """
        out: List[str] = []
        missing: List[str] = []
        for raw in (paths or []):
            target = self.workspaces.resolve_path(workspace or "default", str(raw))
            try:
                rel = target.relative_to(root.resolve()).as_posix()
            except ValueError:
                raise WorkspaceError(f"路径越界: {raw}")
            if not rel:
                continue
            if not target.exists():
                missing.append(rel)
            elif rel not in out:
                out.append(rel)
        if missing:
            raise WorkspaceError(f"以下路径在工作区中不存在: {', '.join(missing[:5])}")
        return out


__all__ = ["GitService", "GitUnavailable", "GIT_TIMEOUT"]
