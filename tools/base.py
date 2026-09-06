"""工具基类、统一返回结构与带重试的注册表。"""
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Type


class ToolError(Exception):
    """工具执行失败（agent 会捕获并决定重试/跳过/重新规划）。"""
    pass


@dataclass
class ToolResult:
    ok: bool
    data: Any = None
    error: str = ""


class Tool:
    name: str = ""
    description: str = ""
    args_desc: Dict[str, str] = {}
    cost: str = "低"            # 成本感知：低/中/高（提示规划器优先低成本）
    avg_seconds: float = 0.5    # 预估耗时

    def run(self, **kwargs) -> dict:
        raise NotImplementedError

    def schema(self) -> str:
        args = ", ".join(f"{k}:{v}" for k, v in self.args_desc.items())
        return f"- {self.name}({args}): {self.description} [成本:{self.cost}, 约{self.avg_seconds:g}s]"


class ToolRegistry:
    def __init__(self, tool_max_retries: int = 2):
        self.tools: Dict[str, Tool] = {}
        self.tool_max_retries = tool_max_retries
        self.call_stats: Dict[str, Dict] = {}   # name -> {calls, fails, retries}

    def register(self, tool: Tool):
        self.tools[tool.name] = tool

    def get(self, name: str) -> Tool:
        if name not in self.tools:
            raise ToolError(f"未知工具: {name}，可用工具: {list(self.tools)}")
        return self.tools[name]

    def call(self, name: str, args: dict, on_retry: Callable = None) -> ToolResult:
        """带指数退避的工具调用；重试过程通过 on_retry 回调对外发射事件。"""
        tool = self.get(name)
        stat = self.call_stats.setdefault(name, {"calls": 0, "fails": 0, "retries": 0})
        stat["calls"] += 1
        last_err = None
        for attempt in range(self.tool_max_retries + 1):
            try:
                data = tool.run(**(args or {}))
                return ToolResult(ok=True, data=data)
            except ToolError as e:
                last_err = e
            except Exception as e:  # 未预期异常也纳入统一重试
                last_err = ToolError(f"{type(e).__name__}: {e}")
            stat["fails"] += 1
            if attempt < self.tool_max_retries:
                stat["retries"] += 1
                if on_retry:
                    on_retry(attempt + 1, str(last_err))
                time.sleep(1.5 * (attempt + 1))
        return ToolResult(ok=False, error=f"工具 {name} 重试{self.tool_max_retries}次后仍失败: {last_err}")

    def prompt_schema(self, exclude: List[str] = None) -> str:
        return "\n".join(t.schema() for n, t in self.tools.items() if n not in (exclude or []))
