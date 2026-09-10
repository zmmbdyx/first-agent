"""Agent 执行相关请求/响应模型（契约 2.1 / 2.3 节）。"""
from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field

# 契约冻结的取值域：前端下拉框与此一一对应，越界即 422，避免脏参数流到图编排层
Preset = Literal["standard", "minimal", "ptc", "creative"]
PermissionMode = Literal["read_only", "workspace_write", "full_access"]
ReasoningEffort = Literal["low", "medium", "high"]


class RunRequest(BaseModel):
    """POST /api/agent/run 请求体（字段名冻结，不得改名）。"""

    task: str = Field(..., max_length=32000)  # 超长输入会打爆上下文，边界就在这里挡住
    session_id: str = ""
    preset: Preset = "standard"
    workspace: str = "default"
    permission_mode: PermissionMode = "workspace_write"
    model: str = ""
    reasoning_effort: ReasoningEffort = "medium"
    priority: int = Field(default=5, ge=0, le=9)
    resume: bool = False


class RunAccepted(BaseModel):
    """入队回执（非流式调用时返回）。"""

    ok: bool = True
    run_id: str = ""
    session_id: str = ""
    status: str = "queued"


class InterruptOut(BaseModel):
    ok: bool = True
    run_id: str = ""


class RunStatusOut(BaseModel):
    run_id: str
    session_id: str = ""
    status: str = "unknown"
    task: str = ""
    preset: str = "standard"
    workspace: str = "default"
    permission_mode: str = "workspace_write"
    model: str = ""
    started_at: str = ""
    finished_at: str = ""
    stats: Dict[str, Any] = Field(default_factory=dict)
    error: str = ""


class PresetOut(BaseModel):
    id: str
    name: str
    description: str = ""
    params: Dict[str, Any] = Field(default_factory=dict)


class PresetListOut(BaseModel):
    items: List[PresetOut] = Field(default_factory=list)


class ServiceUnavailableOut(BaseModel):
    """RunService（图编排侧）尚未就绪时的明确降级响应。"""

    detail: str = ""
    optional: Optional[str] = None
