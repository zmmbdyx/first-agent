"""轨迹记录器：按节点累积思考 / 工具调用 / 耗时 / Token，并产出契约 `trajectory` 事件。

设计权衡
--------
1. **事件驱动 + 节点驱动双入口**：既有事件总线会在工具调用/思考发生时推事件，
   图节点则负责"开始/结束"边界。两者都写进同一个 `NodeRecord`，因此即使某个事件
   漏了（例如工具在中断线程里才返回），轨迹仍然完整可读。
2. **增量推送整份快照**：契约规定 `trajectory` 事件每次携带完整 `nodes[]`。
   这里不做差分——前端按 `node_id` 覆盖即可，逻辑简单且不会出现"漏了一条增量
   导致轨迹错位"的问题。
3. 用 `RLock` 保护：事件可能来自工作线程（工具重试的退避睡眠就在工作线程里），
   而快照由事件循环线程读取。
4. 时间统一用 `time.monotonic()`：只关心相对耗时，不受系统时钟回拨影响。
"""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

# 单个节点最多保留的思考/工具条目（防止长任务把快照撑大）
MAX_ITEMS_PER_NODE = 60


@dataclass
class NodeRecord:
    """一个执行节点的轨迹（字段与契约 `trajectory.nodes[]` 对应）。"""

    node_id: str
    node: str
    label: str
    seq: int
    status: str = "running"          # running / done / failed / skipped / interrupted
    elapsed_ms: int = 0
    tokens: dict = field(default_factory=dict)
    thoughts: list[dict] = field(default_factory=list)
    tool_calls: list[dict] = field(default_factory=list)
    error: str = ""
    started_at: float = field(default_factory=time.monotonic)
    ended_at: float = 0.0
    task_id: str = ""
    tool: str = ""
    detail: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        """输出契约字段，附带若干可选字段（前端可忽略，调试很有用）。"""
        return {"node_id": self.node_id, "node": self.node, "label": self.label,
                "seq": self.seq, "status": self.status, "elapsed_ms": self.elapsed_ms,
                "tokens": dict(self.tokens), "thoughts": list(self.thoughts),
                "tool_calls": list(self.tool_calls), "error": self.error,
                "task_id": self.task_id or None, "tool": self.tool or None,
                "detail": dict(self.detail)}


class TrajectoryRecorder:
    """轨迹记录器。一个 run 一个实例。"""

    def __init__(self, run_id: str = "", session_id: str = ""):
        self.run_id = run_id
        self.session_id = session_id
        self._lock = threading.RLock()
        self._nodes: list[NodeRecord] = []
        self._open: NodeRecord | None = None
        self.seq = 0
        self.current_task_id = ""
        self.current_task_title = ""
        self.current_tool = ""

    # ---------------- 节点生命周期 ----------------
    def start(self, node: str, label: str = "", node_id: str = "",
              task_id: str = "", **detail: Any) -> NodeRecord:
        """开启一个节点。若上一个节点尚未结束（异常路径），自动按 done 收尾。"""
        with self._lock:
            if self._open is not None:
                self.finish(status=self._open.status if self._open.status != "running" else "done")
            self.seq += 1
            record = NodeRecord(node_id=node_id or f"n{self.seq}", node=node,
                                label=label or node, seq=self.seq,
                                task_id=task_id or self.current_task_id,
                                tool=self.current_tool, detail=dict(detail))
            self._nodes.append(record)
            self._open = record
            return record

    def finish(self, status: str = "done", error: str = "", tokens: dict | None = None,
               **detail: Any) -> dict:
        """结束当前节点，返回该节点快照。"""
        with self._lock:
            record = self._open
            if record is None:
                return {}
            now = time.monotonic()
            record.status = status or "done"
            record.elapsed_ms = int(max(0.0, now - record.started_at) * 1000)
            record.ended_at = now
            if error:
                record.error = str(error)[:1000]
            if tokens:
                record.tokens = {**record.tokens, **{k: int(v or 0) for k, v in tokens.items()}}
            if detail:
                record.detail = {**record.detail, **detail}
            self._open = None
            return record.to_dict()

    def add_tokens(self, **tokens: Any) -> None:
        """给当前节点累加 Token（图节点在 LLM 用量回调里调用）。"""
        with self._lock:
            record = self._open
            if record is None:
                return
            for key, value in tokens.items():
                try:
                    record.tokens[key] = int(record.tokens.get(key, 0)) + int(value or 0)
                except (TypeError, ValueError):
                    continue

    def set_tokens(self, tokens: dict | None) -> None:
        with self._lock:
            if self._open is not None and tokens:
                self._open.tokens = {k: int(v or 0) for k, v in tokens.items()}

    # ---------------- 任务上下文（由事件桥接调用） ----------------
    def set_task(self, task_id: Any, title: str = "", tool: str = "") -> None:
        with self._lock:
            self.current_task_id = str(task_id or "")
            self.current_task_title = title or ""
            self.current_tool = tool or ""

    # ---------------- 事件喂数 ----------------
    def record_thought(self, task_id: Any, step: Any, thought: str) -> None:
        with self._lock:
            record = self._open
            if record is None or not (thought or "").strip():
                return
            if len(record.thoughts) < MAX_ITEMS_PER_NODE:
                record.thoughts.append({"task_id": str(task_id or ""),
                                        "step": int(step) if str(step or "").isdigit() else (step or 0),
                                        "thought": str(thought)[:600],
                                        "ts": round(time.time(), 3)})

    def record_tool_call(self, evt: dict) -> None:
        with self._lock:
            record = self._open
            if record is None:
                return
            if len(record.tool_calls) < MAX_ITEMS_PER_NODE:
                record.tool_calls.append({
                    "call_id": evt.get("call_id") or "",
                    "task_id": str(evt.get("task_id") or ""),
                    "tool": evt.get("tool") or "",
                    "args": evt.get("args") or {},
                    "cost": evt.get("cost") or "",
                    "status": "running",
                    "ok": None,
                    "brief": "",
                    "error": "",
                    "elapsed_ms": 0,
                })

    def record_tool_result(self, evt: dict) -> None:
        call_id = evt.get("call_id") or ""
        ok = bool(evt.get("ok", evt.get("type") == "tool_result"))
        with self._lock:
            record = self._open
            if record is None:
                return
            target = None
            for entry in reversed(record.tool_calls):
                if call_id and entry.get("call_id") == call_id:
                    target = entry
                    break
                if not call_id and entry.get("status") == "running":
                    target = entry
                    break
            if target is None:
                if len(record.tool_calls) >= MAX_ITEMS_PER_NODE:
                    return
                target = {"call_id": call_id, "task_id": str(evt.get("task_id") or ""),
                          "tool": evt.get("tool") or "", "args": {}, "cost": "", "status": "running",
                          "ok": None, "brief": "", "error": "", "elapsed_ms": 0}
                record.tool_calls.append(target)
            target["ok"] = ok
            target["status"] = "ok" if ok else "error"
            target["elapsed_ms"] = int(evt.get("elapsed_ms") or 0)
            if ok:
                target["brief"] = str(evt.get("brief") or "")[:400]
            else:
                target["error"] = str(evt.get("error") or "")[:400]

    def record_node_metric(self, evt: dict) -> None:
        """节点结束事件里携带的耗时/Token/错误回填到当前节点（已有值不覆盖）。"""
        with self._lock:
            record = self._open
            if record is None:
                return
            if evt.get("elapsed_ms") and not record.elapsed_ms:
                record.elapsed_ms = int(evt.get("elapsed_ms") or 0)
            if evt.get("tokens"):
                record.tokens = {**{k: int(v or 0) for k, v in (evt.get("tokens") or {}).items()},
                                 **record.tokens}
            if evt.get("error") and not record.error:
                record.error = str(evt.get("error"))[:1000]

    # ---------------- 快照 ----------------
    def snapshot(self, include_open: bool = True) -> dict:
        """完整轨迹快照（契约 `trajectory` 事件载荷）。

        注意字段顺序：`nodes` 必须最后写入，否则会被统计里的同名字段（节点数）覆盖。
        统计字段全部收进 `counters`，与契约字段零冲突。
        """
        with self._lock:
            nodes = [n.to_dict() for n in self._nodes]
            if include_open and self._open is not None:
                for node in nodes:
                    if node["node_id"] == self._open.node_id:
                        node["status"] = "running"
                        node["elapsed_ms"] = int(max(0.0, time.monotonic()
                                                     - self._open.started_at) * 1000)
            return {"run_id": self.run_id, "session_id": self.session_id,
                    "seq": self.seq, "counters": self.counters(), "nodes": nodes}

    def nodes(self, from_seq: int = 1) -> list[dict]:
        """从 `from_seq` 起的节点（含），reduce 合并用。"""
        index = max(0, int(from_seq) - 1)
        with self._lock:
            return [n.to_dict() for n in self._nodes[index:]]

    def last(self) -> dict:
        with self._lock:
            return self._nodes[-1].to_dict() if self._nodes else {}

    def current(self) -> NodeRecord | None:
        return self._open

    # ---------------- 统计 ----------------
    def counters(self) -> dict:
        """统计：节点数、工具调用数、重试数、失败数、整体耗时。"""
        with self._lock:
            nodes = list(self._nodes)
        tools = [c for n in nodes for c in n.tool_calls]
        return {"nodes": len(nodes),
                "tool_calls": len(tools),
                "tool_errors": sum(1 for c in tools if c.get("ok") is False),
                "tool_retries": sum(int(c.get("attempts") or 0) for c in tools),
                "failed_nodes": sum(1 for n in nodes if n.status == "failed"),
                "elapsed_ms": sum(int(n.elapsed_ms or 0) for n in nodes)}

    def summary(self) -> dict:
        """`finalize` 阶段写入 `stats` 的轨迹摘要。"""
        data = self.counters()
        with self._lock:
            data["node_names"] = [n.node for n in self._nodes]
        return data

    def token_totals(self) -> dict:
        with self._lock:
            total: dict[str, int] = {}
            for node in self._nodes:
                for key, value in (node.tokens or {}).items():
                    total[key] = total.get(key, 0) + int(value or 0)
        return total


__all__ = ["TrajectoryRecorder", "NodeRecord", "MAX_ITEMS_PER_NODE"]
