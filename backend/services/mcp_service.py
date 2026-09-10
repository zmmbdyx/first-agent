"""MCP 兼容层：把工具注册表映射为最小可用的 MCP 风格工具协议。

当前只实现「工具发现 + 调用」这一段（`tools/list`、`tools/call`），
足以让外部 MCP 客户端接上本服务；协议版本号用契约里的中性常量 `mcp/1.0`，
不引入任何厂商特定的传输或鉴权实现。
"""
from __future__ import annotations

from typing import Any, Dict, List

from services.tool_service import ToolService

PROTOCOL = "mcp/1.0"
TOOLS_ENDPOINT = "/api/tools"


class MCPService:
    """`GET /api/tools` 的 mcp 元信息与 tools/list、tools/call 两个动作。"""

    def __init__(self, tools: ToolService) -> None:
        self.tools = tools

    def info(self) -> Dict[str, Any]:
        """契约：`{"protocol":"mcp/1.0","tools_endpoint":"/api/tools"}`。"""
        return {"protocol": PROTOCOL, "tools_endpoint": TOOLS_ENDPOINT}

    def list_tools(self) -> List[dict]:
        return self.tools.list()

    def call_tool(self, name: str, args: Dict[str, Any]) -> dict:
        return self.tools.test(name, args or {})

    def descriptors(self) -> List[dict]:
        """MCP 客户端友好的描述形态（name/description/inputSchema）。"""
        return [{"name": t["name"], "description": t["description"],
                 "inputSchema": t["input_schema"]} for t in self.tools.list()]


__all__ = ["MCPService", "PROTOCOL", "TOOLS_ENDPOINT"]
