"""工具服务：内置工具清单 + 自定义工具（MCP 兼容）注册/卸载/试调用。

设计取舍：
- 图编排与既有 Agent 都只认 `core.tools.base.ToolRegistry`，所以自定义工具不是另起一套
  执行路径，而是实现成 `Tool` 适配器后注册进同一个注册表——规划器与 ReAct 立刻可用；
- `kind=python` 不接受配置里的任意源码（那等于开放任意代码执行），只接受
  「转发到已注册工具名」的声明式委派，配置形如 {"delegate": "jd_analyze"}；
- 内置工具禁止卸载（契约要求 403），自定义工具与内置同名时拒绝注册（409）。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from sqlalchemy import select

from config import load_config
from core.agent import JobAgent
from core.tools.base import Tool, ToolError, ToolRegistry
from db import SessionLocal
from models import CustomTool
from models.base import gen_id

# 契约：内置工具来源标记
SOURCE_BUILTIN = "builtin"
SOURCE_CUSTOM = "custom"
# kind ∈ http | python | mcp
KINDS = ("http", "python", "mcp")
HTTP_TIMEOUT = 30


def _schema_of(payload: Dict[str, Any]) -> Dict[str, Any]:
    """input_schema 兜底：MCP 客户端允许省略，规范化为对象型 JSON Schema。"""
    schema = payload.get("input_schema")
    if not isinstance(schema, dict) or not schema:
        return {"type": "object", "properties": {}}
    return schema


class HTTPTool(Tool):
    """`kind=http`：把任意 HTTP 端点包装成工具（自建服务、内网网桥、兼容协议网关均可）。"""

    source = SOURCE_CUSTOM

    def __init__(self, name: str, description: str, config: Dict[str, Any]) -> None:
        self.name = name
        self.description = description or f"自定义 HTTP 工具 {name}"
        self.config = dict(config or {})
        self.cost = str(self.config.get("cost") or "中")
        try:
            self.avg_seconds = float(self.config.get("avg_seconds") or 1.0)
        except (TypeError, ValueError):
            self.avg_seconds = 1.0
        props = self.config.get("input_schema") or {}
        self.args_desc = {k: str(v) for k, v in (props.get("properties") or {}).items()}

    def run(self, **kwargs) -> dict:
        url = str(self.config.get("url") or "").strip()
        if not url:
            raise ToolError(f"工具 {self.name} 未配置 url")
        method = str(self.config.get("method") or "POST").upper()
        headers = {str(k): str(v) for k, v in (self.config.get("headers") or {}).items()}
        body = dict(kwargs or {})
        try:
            import httpx
        except ImportError:
            raise ToolError("缺少 httpx 依赖，无法调用 HTTP 工具")
        try:
            with httpx.Client(timeout=HTTP_TIMEOUT) as client:
                if method == "GET":
                    resp = client.get(url, params=body, headers=headers)
                else:
                    resp = client.request(method, url, json=body, headers=headers)
            if resp.status_code >= 400:
                raise ToolError(f"HTTP {resp.status_code}: {resp.text[:300]}")
            try:
                return resp.json()
            except ValueError:
                return {"text": resp.text[:20000]}
        except ToolError:
            raise
        except Exception as e:
            raise ToolError(f"HTTP 工具调用失败: {type(e).__name__}: {e}")


class DelegateTool(Tool):
    """`kind=python`：声明式委派——把调用转给注册表里已有的工具，不执行任意源码。"""

    source = SOURCE_CUSTOM

    def __init__(self, name: str, description: str, target: str, config: Dict[str, Any]) -> None:
        self.name = name
        self.description = description or f"自定义工具 {name}（委派给 {target}）"
        self.target = target
        self.config = dict(config or {})
        self.cost = str(self.config.get("cost") or "低")
        self.avg_seconds = float(self.config.get("avg_seconds") or 0.5)
        self.args_desc = {}

    def run(self, **kwargs) -> dict:
        raise ToolError(
            f"工具 {self.name} 需要在注册表中委派执行（目标: {self.target}）")


class ToolService:
    """工具注册表与自定义工具持久化的统一入口。"""

    def __init__(self, cfg=None, agent: Optional[JobAgent] = None) -> None:
        self.cfg = cfg or load_config()
        self.agent = agent or JobAgent(self.cfg)
        self._load_custom()

    # ---------------- 注册表 ----------------
    def registry(self) -> ToolRegistry:
        return self.agent.registry

    def _load_custom(self) -> None:
        """启动时把库里启用的自定义工具装载进注册表（失败不影响内置工具可用）。"""
        try:
            with SessionLocal() as db:
                rows = db.scalars(select(CustomTool)).all()
                items = [{"name": r.name, "description": r.description, "kind": r.kind,
                          "config": dict(r.config or {}), "enabled": bool(r.enabled)}
                         for r in rows]
        except Exception:
            return
        for item in items:
            if not item["enabled"]:
                continue
            self._install(item)

    def _install(self, item: Dict[str, Any]) -> None:
        name = item["name"]
        kind = item.get("kind") or "http"
        config = item.get("config") or {}
        if name in self.agent.registry.tools:
            return  # 不覆盖已存在的工具（内置优先）
        if kind == "http" and str(config.get("url") or "").strip():
            self.agent.registry.register(HTTPTool(name, item.get("description", ""), config))
        elif kind == "python" and str(config.get("delegate") or "").strip():
            self.agent.registry.register(DelegateTool(name, item.get("description", ""),
                                                      str(config["delegate"]), config))

    # ---------------- 查询 ----------------
    def list(self) -> List[dict]:
        """内置 + 自定义工具清单（ToolOut 字段与契约一致）。"""
        custom: Dict[str, dict] = {}
        try:
            with SessionLocal() as db:
                for r in db.scalars(select(CustomTool)).all():
                    custom[r.name] = {"description": r.description or "",
                                      "input_schema": dict(r.input_schema or {}),
                                      "kind": r.kind, "enabled": bool(r.enabled),
                                      "config": dict(r.config or {})}
        except Exception:
            custom = {}
        items: List[dict] = []
        for name, tool in self.agent.registry.tools.items():
            meta = custom.get(name)
            items.append({
                "name": name, "description": getattr(tool, "description", "") or "",
                "input_schema": (meta or {}).get("input_schema") or _schema_of(
                    {"input_schema": {"type": "object", "properties": {
                        k: {"type": "string", "description": v}
                        for k, v in (getattr(tool, "args_desc", {}) or {}).items()}}}),
                "cost": getattr(tool, "cost", "低"),
                "avg_seconds": float(getattr(tool, "avg_seconds", 0.5) or 0.5),
                "source": SOURCE_CUSTOM if meta or getattr(tool, "source", "") == SOURCE_CUSTOM
                          else SOURCE_BUILTIN,
                "enabled": (meta or {}).get("enabled", True),
                "kind": (meta or {}).get("kind") or "builtin",
            })
        return items

    def is_builtin(self, name: str) -> bool:
        with SessionLocal() as db:
            row = db.scalars(select(CustomTool).where(CustomTool.name == name)).first()
            return row is None

    # ---------------- 注册 / 卸载 ----------------
    def register(self, payload: Dict[str, Any]) -> dict:
        name = str(payload.get("name") or "").strip()
        kind = str(payload.get("kind") or "http").strip().lower()
        config = dict(payload.get("config") or {})
        if not name:
            raise ValueError("工具名不能为空")
        if kind not in KINDS:
            raise ValueError(f"kind 必须是 {'/'.join(KINDS)} 之一")
        if kind == "http" and not str(config.get("url") or "").strip():
            raise ValueError("kind=http 必须提供 config.url")
        if kind == "python" and not str(config.get("delegate") or "").strip():
            raise ValueError("kind=python 只支持声明式委派，请提供 config.delegate（已注册工具名）")
        if kind == "python" and str(config.get("delegate")) not in self.agent.registry.tools:
            raise ValueError(f"config.delegate 指向的工具不存在: {config.get('delegate')}")
        with SessionLocal() as db:
            if db.scalars(select(CustomTool).where(CustomTool.name == name)).first():
                raise FileExistsError(f"工具名已存在: {name}")
            if name in self.agent.registry.tools:  # 与内置同名也不允许覆盖
                raise FileExistsError(f"工具名与内置工具冲突: {name}")
            row = CustomTool(id=gen_id(), name=name,
                             description=str(payload.get("description") or ""),
                             input_schema=_schema_of(payload), kind=kind, config=config,
                             enabled=bool(payload.get("enabled", True)))
            db.add(row)
            db.commit()
            item = {"name": name, "description": row.description, "input_schema": row.input_schema,
                    "kind": kind, "config": config, "enabled": row.enabled}
        if item["enabled"]:
            self._install(item)
        return self._out(name, item)

    def unregister(self, name: str) -> bool:
        with SessionLocal() as db:
            row = db.scalars(select(CustomTool).where(CustomTool.name == name)).first()
            if row is None:
                return False
            db.delete(row)
            db.commit()
        self.agent.registry.tools.pop(name, None)
        return True

    # ---------------- 试调用 ----------------
    def test(self, name: str, args: Dict[str, Any]) -> dict:
        """契约：`POST /api/tools/{name}/test` → `{"ok":true,"data":{}}`，失败也返回 ok=false。"""
        tool = self.agent.registry.tools.get(name)
        if tool is None:
            raise KeyError(name)
        call_name = name
        if isinstance(tool, DelegateTool):
            target = self.agent.registry.tools.get(tool.target)
            if target is None:
                return {"ok": False, "data": None, "error": f"委派目标不存在: {tool.target}"}
            call_name = tool.target
        try:
            result = self.agent.registry.call(call_name, dict(args or {}))
        except Exception as e:  # 注册表已兜底，这里只防适配器自身异常
            return {"ok": False, "data": None, "error": f"{type(e).__name__}: {e}"}
        if result.ok:
            data = result.data
            return {"ok": True, "data": data if isinstance(data, dict) else {"result": data},
                    "error": ""}
        return {"ok": False, "data": None, "error": result.error}

    def _out(self, name: str, item: dict) -> dict:
        return {"name": name, "description": item.get("description", ""),
                "input_schema": item.get("input_schema") or {},
                "cost": "中" if item.get("kind") == "http" else "低",
                "avg_seconds": 1.0 if item.get("kind") == "http" else 0.5,
                "source": SOURCE_CUSTOM, "enabled": bool(item.get("enabled", True)),
                "kind": item.get("kind") or "http"}


__all__ = ["ToolService", "HTTPTool", "DelegateTool", "SOURCE_BUILTIN", "SOURCE_CUSTOM", "KINDS"]
