"""会话相关请求/响应模型（字段与架构契约 2.3 节逐一对应）。"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from schemas.common import ORMModel


class SessionCreate(BaseModel):
    title: str = ""
    workspace: str = "default"
    preset: str = "standard"
    permission_mode: str = "workspace_write"
    model: str = ""


class SessionRename(BaseModel):
    title: str = Field(..., max_length=200)


class SessionPin(BaseModel):
    pinned: bool = False


class SessionOut(ORMModel):
    """契约字段：id,title,status,workspace,preset,permission_mode,model,pinned,
    created_at,updated_at,message_count,token_stats{},facts{}"""

    id: str
    title: str
    status: str
    workspace: str
    preset: str
    permission_mode: str
    model: str
    pinned: bool
    created_at: str
    updated_at: str
    message_count: int
    token_stats: Dict[str, Any] = Field(default_factory=dict)
    facts: Dict[str, Any] = Field(default_factory=dict)
    # 滚动摘要：契约表中有 summary 列，回传给前端只为可诊断，不参与布局
    summary: str = ""


class SessionListOut(BaseModel):
    items: List[SessionOut] = Field(default_factory=list)
    total: int = 0


class MessageOut(ORMModel):
    id: int
    role: str
    content: str
    ts: float
    run_id: str = ""
    tokens: Dict[str, Any] = Field(default_factory=dict)


class HistoryOut(BaseModel):
    """契约字段：session / messages / tasks / tool_calls / trajectory / stats。"""

    session: SessionOut
    messages: List[MessageOut] = Field(default_factory=list)
    tasks: List[Dict[str, Any]] = Field(default_factory=list)
    tool_calls: List[Dict[str, Any]] = Field(default_factory=list)
    trajectory: List[Dict[str, Any]] = Field(default_factory=list)
    stats: Dict[str, Any] = Field(default_factory=dict)


class CheckpointIn(BaseModel):
    """回滚请求：checkpoint_id 为空表示回滚到本 run 起点。

    契约只强制 `checkpoint_id`；`run_id` 为可选补充，便于定位到具体检查点。
    """

    checkpoint_id: str = ""
    run_id: str = ""


class RollbackOut(BaseModel):
    ok: bool = True
    session_id: str = ""
    checkpoint_id: str = ""
    detail: Optional[str] = None
