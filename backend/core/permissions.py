"""权限模式解析：read_only / workspace_write / full_access。

设计权衡
--------
1. 权限是**能力集合**而不是一个枚举：沙箱读文件、写文件、跑命令分别检查不同位，
   这样 `read_only` 下"能列目录但不能写"这类组合不必在调用点写 if-else。
2. `full_access` 是**危险能力**，必须由配置显式授权（`ALLOW_FULL_ACCESS=true`）：
   未授权时按 `workspace_write` 执行并给出告警（降级而非报错），保证请求不失败、
   前端也能从返回值里看到真实生效的模式。
3. 沙箱总开关 `SANDBOX_ENABLED=false` 时同样收敛写/执行能力：关掉沙箱不等于放开越界访问。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

from config import Settings

logger = logging.getLogger(__name__)

READ_ONLY = "read_only"
WORKSPACE_WRITE = "workspace_write"
FULL_ACCESS = "full_access"

MODES = (READ_ONLY, WORKSPACE_WRITE, FULL_ACCESS)
DEFAULT_MODE = WORKSPACE_WRITE

# 中文标签：下发给前端展示（与契约 5.4 的「只读 / 工作区可写 / 完全访问」一致）
MODE_LABELS: dict[str, str] = {
    READ_ONLY: "只读",
    WORKSPACE_WRITE: "工作区可写",
    FULL_ACCESS: "完全访问",
}


@dataclass(frozen=True)
class Permission:
    """一次运行生效的能力集合。frozen 以免节点中途被改写（越权风险）。"""

    mode: str
    can_read: bool
    can_write: bool
    can_exec: bool
    allow_outside_workspace: bool = False
    downgraded: bool = False          # 是否由 full_access 降级而来
    warning: str = ""                 # 降级告警（可读中文）

    @property
    def label(self) -> str:
        return MODE_LABELS.get(self.mode, self.mode)

    def to_dict(self) -> dict:
        return {"mode": self.mode, "label": self.label, "can_read": self.can_read,
                "can_write": self.can_write, "can_exec": self.can_exec,
                "allow_outside_workspace": self.allow_outside_workspace,
                "downgraded": self.downgraded, "warning": self.warning}


def normalize_mode(mode: str | None) -> str:
    """非法/空值一律回落默认模式（API 参数拼错不应 500）。"""
    key = (mode or "").strip().lower()
    return key if key in MODES else DEFAULT_MODE


def resolve_permission(mode: str | None, cfg: Settings) -> Permission:
    """把请求里的权限模式解析成生效能力。

    `full_access` 在 `cfg.allow_full_access=False` 时**降级为 workspace_write 并告警**。
    """
    resolved = normalize_mode(mode)
    sandbox_on = bool(getattr(cfg, "sandbox_enabled", True))
    warning = ""

    if resolved == FULL_ACCESS and not bool(getattr(cfg, "allow_full_access", False)):
        warning = ("请求了完全访问，但配置未开启 ALLOW_FULL_ACCESS，已降级为「工作区可写」；"
                   "如需越界访问，请显式设置 ALLOW_FULL_ACCESS=true 后重试。")
        logger.warning(warning)
        resolved = WORKSPACE_WRITE
        downgraded = True
    else:
        downgraded = False

    if resolved == READ_ONLY:
        return Permission(mode=resolved, can_read=True, can_write=False, can_exec=False,
                          allow_outside_workspace=False, downgraded=downgraded, warning=warning)

    if resolved == FULL_ACCESS:
        # 已显式授权：允许工作区外访问；沙箱关闭时同样放开
        return Permission(mode=resolved, can_read=True, can_write=True, can_exec=True,
                          allow_outside_workspace=True, downgraded=False, warning="")

    # workspace_write：写/执行都限定在工作区内
    exec_allowed = sandbox_on
    extra = ""
    if not sandbox_on:
        extra = "沙箱已关闭（SANDBOX_ENABLED=false），命令执行能力被收敛；文件读写仍限定在工作区内。"
        warning = (warning + " " + extra).strip()
    return Permission(mode=WORKSPACE_WRITE, can_read=True, can_write=True, can_exec=exec_allowed,
                      allow_outside_workspace=False, downgraded=downgraded, warning=warning)


__all__ = ["Permission", "READ_ONLY", "WORKSPACE_WRITE", "FULL_ACCESS", "MODES",
           "DEFAULT_MODE", "MODE_LABELS", "resolve_permission", "normalize_mode"]
