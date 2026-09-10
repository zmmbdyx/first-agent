"""工具（MCP 兼容）请求/响应模型（契约 2.4 节）。

`ToolOut`：name,description,input_schema,cost,avg_seconds,source,enabled,kind
"""
from __future__ import annotations

from typing import Any, Dict, Literal

from pydantic import BaseModel, Field

ToolKind = Literal["http", "python", "mcp"]


class ToolOut(BaseModel):
    name: str
    description: str = ""
    input_schema: Dict[str, Any] = Field(default_factory=dict)
    cost: str = "低"
    avg_seconds: float = 0.5
    source: Literal["builtin", "custom"] = "builtin"
    enabled: bool = True
    kind: str = "builtin"


class MCPInfo(BaseModel):
    protocol: str = "mcp/1.0"
    tools_endpoint: str = "/api/tools"


class ToolListOut(BaseModel):
    items: list[ToolOut] = Field(default_factory=list)
    mcp: MCPInfo = Field(default_factory=MCPInfo)


class ToolRegister(BaseModel):
    """MCP 工具描述 + 绑定方式。"""

    name: str = Field(..., min_length=1, max_length=80)
    description: str = ""
    input_schema: Dict[str, Any] = Field(default_factory=dict)
    kind: ToolKind = "http"
    config: Dict[str, Any] = Field(default_factory=dict)
    enabled: bool = True


class ToolTestIn(BaseModel):
    args: Dict[str, Any] = Field(default_factory=dict)


class ToolTestOut(BaseModel):
    ok: bool = True
    data: Any = None
    error: str = ""
