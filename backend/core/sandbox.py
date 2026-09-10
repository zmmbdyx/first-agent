"""沙箱执行环境：路径限定（不得逃逸工作区）+ 命令白名单 + 超时 + 输出截断。

设计权衡
--------
1. **先解析再比较**：所有路径都经 `Path.resolve()` 规范化（消除 `..`、`.` 与符号链接），
   再用 `relative_to()` 判断是否落在允许根内。字符串前缀判断会被 `workspaces-evil/`
   这类同前缀目录绕过，因此不采用。
2. **权限来自 `Permission` 而不是硬编码**：读/写/执行分别校验，越权时抛
   `SandboxViolation`（携带可读中文原因），由上层决定是报错还是降级。
3. **命令白名单**：只放行常见的只读/构建类命令，并且**一律以 list 形式执行**
   （不经 shell），从而天然杜绝 `;` `|` `&&` 注入；含 shell 元字符的入参直接拒绝。
4. **环境变量最小化**：给子进程的环境里剔除 `*_KEY` / `*_TOKEN` / `*_SECRET` /
   `*_PASSWORD` 等凭据变量，避免沙箱内命令把密钥写进日志或产物。
5. 本模块不启动任何后台进程，全部用 `subprocess.run` 同步执行（有超时护栏）。
"""
from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence

from config import Settings
from core.permissions import Permission, resolve_permission

logger = logging.getLogger(__name__)


class SandboxViolation(Exception):
    """沙箱拒绝执行（路径越界 / 权限不足 / 命令不在白名单）。message 为可读中文原因。"""


# 允许执行的命令（不含路径，比对时取 basename 并去掉 .exe 后缀，大小写不敏感）
DEFAULT_COMMAND_ALLOWLIST: frozenset[str] = frozenset({
    "python", "python3", "py", "pip", "pytest",
    "git", "node", "npm", "pnpm", "npx",
    "dir", "ls", "cat", "type", "find", "findstr", "grep", "head", "tail", "wc",
    "echo", "where", "which", "tree", "stat", "du", "sort", "uniq", "diff",
    "mkdir", "copy", "cp", "move", "mv", "del", "rm",
})

# shell 元字符：以 list 方式执行本就不经 shell，这里额外拒绝，避免"看起来像注入"的入参
_SHELL_META_RE = re.compile(r"[;&|<>`$^\n\r]|\|\||&&")

# 子进程环境变量中需要剔除的凭据键（大小写不敏感）
_SECRET_ENV_RE = re.compile(r"(KEY|TOKEN|SECRET|PASSWORD|PASSWD|CREDENTIAL)", re.I)

# 允许保留的最小环境（Windows 需要 SYSTEMROOT 等才能正常起进程）
_ENV_KEEP = ("PATH", "SYSTEMROOT", "SYSTEMDRIVE", "WINDIR", "TEMP", "TMP", "TMPDIR",
             "PATHEXT", "COMSPEC", "HOME", "USERPROFILE", "LANG", "LC_ALL", "PYTHONPATH",
             "PYTHONIOENCODING", "NUMBER_OF_PROCESSORS", "OS")


@dataclass(frozen=True)
class CommandResult:
    """命令执行结果。stdout/stderr 均已按 `sandbox_max_output` 截断。"""

    ok: bool
    exit_code: int
    stdout: str = ""
    stderr: str = ""
    elapsed_ms: int = 0
    truncated: bool = False
    error: str = ""

    def to_dict(self) -> dict:
        return {"ok": self.ok, "exit_code": self.exit_code, "stdout": self.stdout,
                "stderr": self.stderr, "elapsed_ms": self.elapsed_ms,
                "truncated": self.truncated, "error": self.error}


@dataclass(frozen=True)
class DirEntry:
    name: str
    path: str
    type: str          # "file" | "dir"
    size: int

    def to_dict(self) -> dict:
        return {"name": self.name, "path": self.path, "type": self.type, "size": self.size}


class Sandbox:
    """工作区限定的文件 + 命令执行环境。

    用法::

        sb = Sandbox(cfg, permission=resolve_permission("workspace_write", cfg))
        text = sb.read_text(workspace, "notes/a.md")
        res = sb.run_command(["python", "-V"], cwd=workspace, timeout=10)
    """

    def __init__(self, cfg: Settings, permission: Permission | None = None,
                 workspace_root: Path | None = None,
                 extra_roots: Iterable[Path] | None = None,
                 allowlist: Iterable[str] | None = None):
        self.cfg = cfg
        self.permission = permission or resolve_permission(None, cfg)
        root = workspace_root or Path(getattr(cfg, "workspace_path", Path.cwd()))
        try:
            self.workspace_root = Path(root).expanduser().resolve()
        except OSError:  # 极端路径异常时退回未解析形态，后续校验仍会拦
            self.workspace_root = Path(root)
        self.extra_roots = self._normalize_roots(extra_roots or [])
        self.max_output = int(getattr(cfg, "sandbox_max_output", 65536) or 65536)
        self.default_timeout = int(getattr(cfg, "sandbox_timeout", 300) or 300)
        self.allowlist = frozenset(x.lower() for x in (allowlist or DEFAULT_COMMAND_ALLOWLIST))

    # ---------------- 内部工具 ----------------
    @staticmethod
    def _normalize_roots(roots: Iterable[Path]) -> tuple[Path, ...]:
        out: list[Path] = []
        for r in roots:
            try:
                out.append(Path(r).expanduser().resolve())
            except OSError:
                continue
        return tuple(out)

    def _allowed_roots(self) -> tuple[Path, ...]:
        """可写/可读的根集合。完全访问时额外允许工作区外（但没有根的"任意路径"，
        因此越界判断退化为"不校验前缀"，由调用方显式承担风险）。"""
        if self.permission.allow_outside_workspace:
            return ()
        return (self.workspace_root,) + self.extra_roots

    def _safe_resolve(self, candidate: str | Path) -> Path:
        p = Path(candidate).expanduser()
        try:
            return p.resolve()
        except OSError as e:  # 路径过长/非法字符等
            raise SandboxViolation(f"路径无法解析：{candidate}（{e}）") from e

    def _within(self, path: Path, root: Path) -> bool:
        try:
            path.relative_to(root)
            return True
        except ValueError:
            return False

    def _inside_any_root(self, path: Path) -> bool:
        return any(self._within(path, r) for r in self._allowed_roots())

    # ---------------- 路径解析（对外核心方法） ----------------
    def resolve_path(self, workspace: Path, candidate: str) -> Path:
        """把候选路径解析成绝对路径，并保证落在允许范围内。

        - `candidate` 为相对路径时相对 `workspace` 解析；绝对路径按原样解析；
        - 允许范围内的**不存在**路径也会被返回（写新文件要用），此时以其最近存在父目录判定；
        - 越界抛 `SandboxViolation`。
        """
        if candidate is None or str(candidate).strip() == "":
            raise SandboxViolation("路径为空，无法解析")
        raw = str(candidate).strip().strip('"').strip("'")
        try:
            ws = Path(workspace).expanduser().resolve()
        except OSError:
            ws = Path(workspace)

        p = Path(raw)
        target = self._safe_resolve(p if p.is_absolute() else ws / p)

        if self.permission.allow_outside_workspace:
            return target

        if not ws.is_absolute() or not ws.exists():
            # 工作区本身不存在：仍然允许（会按父目录判定），但必须以它为根
            pass
        if not self._inside_any_root(target) and not self._within(target, ws):
            raise SandboxViolation(
                f"路径越界：{raw} → {target} 不在允许的根（{self.workspace_root}）之内；"
                "如需访问工作区外路径，需工作区可写以上的权限且显式配置允许。")
        return target

    def _check_read(self) -> None:
        if not self.permission.can_read:
            raise SandboxViolation(f"权限不足：当前模式「{self.permission.label}」不允许读取文件")

    def _check_write(self) -> None:
        if not self.permission.can_write:
            raise SandboxViolation(
                f"权限不足：当前模式「{self.permission.label}」不允许写入文件，"
                "请切换到「工作区可写」或「完全访问」。")

    def _check_exec(self) -> None:
        if not self.permission.can_exec:
            raise SandboxViolation(
                f"权限不足：当前模式「{self.permission.label}」不允许执行命令（沙箱已收敛执行能力）")

    # ---------------- 文件操作 ----------------
    def read_text(self, workspace: Path, candidate: str, max_bytes: int | None = None,
                  encoding: str = "utf-8") -> str:
        """读取工作区内文本文件；超出 `max_bytes`（默认取 sandbox_max_output）截断。"""
        self._check_read()
        path = self.resolve_path(workspace, candidate)
        if not path.exists():
            raise SandboxViolation(f"文件不存在：{candidate}")
        if path.is_dir():
            raise SandboxViolation(f"目标是目录而非文件：{candidate}")
        limit = int(max_bytes if max_bytes is not None else self.max_output)
        try:
            data = path.read_bytes()
        except OSError as e:
            raise SandboxViolation(f"读取失败：{candidate}（{e}）") from e
        if len(data) > limit:
            data = data[:limit]
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            return data.decode(encoding, errors="replace")

    def write_text(self, workspace: Path, candidate: str, content: str,
                   encoding: str = "utf-8", mkdir: bool = True) -> Path:
        """写入工作区内文本文件，返回落盘绝对路径。"""
        self._check_write()
        path = self.resolve_path(workspace, candidate)
        if path.exists() and path.is_dir():
            raise SandboxViolation(f"目标是目录，无法写入：{candidate}")
        try:
            if mkdir:
                path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding=encoding)
        except OSError as e:
            raise SandboxViolation(f"写入失败：{candidate}（{e}）") from e
        return path

    def list_dir(self, workspace: Path, candidate: str = ".", depth: int = 1,
                 max_entries: int = 500) -> list[DirEntry]:
        """列出目录内容（depth=1 只列一层；depth<=0 视为无限但受 max_entries 限制）。"""
        self._check_read()
        base = self.resolve_path(workspace, candidate)
        if not base.is_dir():
            raise SandboxViolation(f"目录不存在：{candidate}")
        out: list[DirEntry] = []

        def walk(d: Path, level: int) -> None:
            if len(out) >= max_entries:
                return
            try:
                children = sorted(d.iterdir(), key=lambda p: (p.is_file(), p.name.lower()))
            except OSError:
                return
            for child in children:
                if len(out) >= max_entries:
                    return
                if not self._inside_any_root(self._safe_resolve(child)):
                    continue  # 符号链接指向工作区外：不列出，避免引导越界读取
                is_dir = child.is_dir()
                try:
                    size = 0 if is_dir else child.stat().st_size
                except OSError:
                    size = 0
                out.append(DirEntry(name=child.name, path=str(child), type="dir" if is_dir else "file",
                                    size=size))
                if is_dir and (depth <= 0 or level + 1 < depth):
                    walk(child, level + 1)

        walk(base, 0)
        return out

    # ---------------- 命令执行 ----------------
    def _command_env(self) -> dict[str, str]:
        env: dict[str, str] = {}
        for key in _ENV_KEEP:
            value = os.environ.get(key)
            if value:
                env[key] = value
        for key, value in os.environ.items():
            if key in env or _SECRET_ENV_RE.search(key):
                continue
            env[key] = value  # 非凭据变量保留，保证虚拟环境/编码等设置可用
        env.setdefault("PYTHONIOENCODING", "utf-8")
        return env

    def _resolve_executable(self, cmd: Sequence[str]) -> str:
        raw = str(cmd[0]).strip()
        if not raw:
            raise SandboxViolation("命令为空")
        if _SHELL_META_RE.search(raw):
            raise SandboxViolation(f"命令包含被禁用的 shell 元字符：{raw}")
        name = Path(raw).name.lower()
        if name.endswith(".exe"):
            name = name[:-4]
        if name not in self.allowlist:
            raise SandboxViolation(
                f"命令不在白名单内：{name}（允许：{'、'.join(sorted(self.allowlist))}）")
        target = raw if (os.sep in raw or "/" in raw) else (shutil.which(raw) or "")
        if not target:
            raise SandboxViolation(f"找不到可执行文件：{raw}")
        return target

    def run_command(self, cmd: list[str], cwd: Path, timeout: int | None = None,
                    max_output: int | None = None) -> CommandResult:
        """执行命令：白名单校验 → 工作目录限定 → `subprocess.run` + 超时 + 输出截断。

        任何拒绝都抛 `SandboxViolation`（可读中文原因）；命令本身执行失败则返回
        `ok=False` 的结果（这不是越权，不应中断上游编排）。
        """
        if not isinstance(cmd, (list, tuple)) or not cmd:
            raise SandboxViolation("命令必须是「可执行文件 + 参数」的非空列表（不接受整串命令）")
        self._check_exec()
        parts = [str(x) for x in cmd]
        for arg in parts:
            if _SHELL_META_RE.search(arg):
                raise SandboxViolation(f"参数包含被禁用的 shell 元字符：{arg}")

        work_dir = self.resolve_path(cwd, ".")
        if not work_dir.exists() or not work_dir.is_dir():
            work_dir = self.workspace_root if self.workspace_root.is_dir() else Path.cwd()
        if not self.permission.allow_outside_workspace and not self._within(work_dir, self.workspace_root):
            raise SandboxViolation(f"工作目录越界：{work_dir} 不在工作区内")

        exe = self._resolve_executable(parts)
        limit = int(max_output if max_output is not None else self.max_output)
        default_timeout = getattr(self.cfg, "task_timeout", 300) or 300
        wait = int(timeout if timeout is not None else self.default_timeout or default_timeout)
        wait = max(1, wait)

        t0 = time.time()
        try:
            proc = subprocess.run([exe, *parts[1:]], cwd=str(work_dir), env=self._command_env(),
                                  capture_output=True, timeout=wait, check=False)
        except subprocess.TimeoutExpired as e:
            partial = self._decode(getattr(e, "stdout", b""))[:limit]
            return CommandResult(ok=False, exit_code=-1, stdout=partial,
                                 stderr=f"命令超时（{wait}s）已被终止",
                                 elapsed_ms=int((time.time() - t0) * 1000),
                                 truncated=len(partial) >= limit, error="timeout")
        except OSError as e:
            return CommandResult(ok=False, exit_code=-1, stderr=str(e),
                                 elapsed_ms=int((time.time() - t0) * 1000),
                                 error=f"{type(e).__name__}: {e}")

        out_raw = self._decode(proc.stdout)
        err_raw = self._decode(proc.stderr)
        truncated = len(out_raw) > limit or len(err_raw) > limit
        return CommandResult(ok=proc.returncode == 0, exit_code=int(proc.returncode),
                             stdout=out_raw[:limit], stderr=err_raw[:limit],
                             elapsed_ms=int((time.time() - t0) * 1000), truncated=truncated)

    @staticmethod
    def _decode(data) -> str:
        if data is None:
            return ""
        if isinstance(data, str):
            return data
        return bytes(data).decode("utf-8", errors="replace")

    def status(self) -> dict:
        """供 `/api/health` 展示沙箱状态（不泄露任何凭据）。"""
        return {"enabled": bool(getattr(self.cfg, "sandbox_enabled", True)),
                "workspace_root": str(self.workspace_root),
                "max_output": self.max_output,
                "timeout": self.default_timeout,
                "commands": len(self.allowlist),
                "permission": self.permission.to_dict()}


__all__ = ["Sandbox", "SandboxViolation", "CommandResult", "DirEntry",
           "DEFAULT_COMMAND_ALLOWLIST"]
