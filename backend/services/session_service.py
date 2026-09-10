"""会话服务：会话 CRUD、历史聚合、以及 JobAgent 运行态与数据库之间的双向搬运。

三个关键设计：
1. 运行态附加信息（artifacts/pending_question/waiting_task_id/…）放进 `sessions.facts`
   的**下划线保留键**里。既有 `core.agent` 已经在所有对外/上下文出口过滤 `_` 前缀键，
   因此既不会污染用户画像，也不必新增契约外的表；
2. `save_runtime()` 幂等地整表重写该会话的消息与任务，避免一次运行多次保存产生重复行；
3. 旧版 `data/sessions/*.json` 存档在首次列表/查询时惰性导入一次，保证重构前后历史不丢。
"""
from __future__ import annotations

import json
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, List, Optional, Tuple

from sqlalchemy import func, or_, select

from config import load_config
from models import (Message, Run, Session as SessionRow, Task as TaskRow, ToolCall,
                    TrajectoryNode)
from models.base import gen_id, iso, utcnow

# facts 中保存运行态信息的保留键前缀（core.agent 会过滤掉所有 _ 前缀键）
RUNTIME_KEY = "_runtime"
# 单次运行落库的产物体积上限：长 JD 全文进上下文有意义，但没必要整份入库
MAX_ARTIFACT_CHARS = 4000
MAX_ARTIFACTS = 12
MAX_EVENT_LOG = 50

_import_lock = threading.Lock()
_legacy_imported = False


def _cap(value: Any, limit: int = MAX_ARTIFACT_CHARS) -> Any:
    """产物体积护栏：只截断字符串与超长列表，保持 JSON 可序列化。"""
    if isinstance(value, str):
        return value[:limit]
    if isinstance(value, list):
        return [_cap(v, limit) for v in value[:MAX_ARTIFACTS]]
    if isinstance(value, dict):
        return {k: _cap(v, limit) for k, v in list(value.items())[:40]}
    return value


def _row_to_dict(row: SessionRow) -> dict:
    """会话行 → SessionOut 字段（时间转 ISO 字符串，事实过滤运行态保留键）。"""
    facts = {k: v for k, v in (row.facts or {}).items() if k != RUNTIME_KEY}
    return {
        "id": row.id, "title": row.title, "status": row.status,
        "workspace": row.workspace, "preset": row.preset,
        "permission_mode": row.permission_mode, "model": row.model,
        "pinned": bool(row.pinned),
        "created_at": iso(row.created_at), "updated_at": iso(row.updated_at),
        "message_count": int(row.message_count or 0),
        "token_stats": dict(row.token_stats or {}), "facts": facts,
        "summary": row.summary or "",
    }


class SessionService:
    """构造函数保持无参，方便路由层按请求创建（db 会话由调用方传入）。"""

    def __init__(self, cfg=None) -> None:
        self.cfg = cfg or load_config()

    # ================= 会话 CRUD =================
    def create(self, title: str = "", workspace: str = "default", preset: str = "standard",
               db=None, permission_mode: str = "workspace_write", model: str = "") -> dict:
        from db import SessionLocal
        own = db is None
        db = db or SessionLocal()
        try:
            row = SessionRow(
                id=gen_id(), title=(title or "新会话")[:200], status="idle",
                workspace=workspace or "default", preset=preset or "standard",
                permission_mode=permission_mode or "workspace_write",
                model=model or self._default_model(), pinned=False,
                facts={}, summary="", token_stats={}, message_count=0,
                created_at=utcnow(), updated_at=utcnow())
            db.add(row)
            db.commit()
            return _row_to_dict(row)
        finally:
            if own:
                db.close()

    def list(self, workspace: Optional[str] = None, limit: int = 50, offset: int = 0,
             q: Optional[str] = None, db=None) -> Tuple[List[dict], int]:
        from db import SessionLocal
        own = db is None
        db = db or SessionLocal()
        try:
            self._import_legacy(db)
            stmt = select(SessionRow)
            if workspace:
                stmt = stmt.where(SessionRow.workspace == workspace)
            if q:
                like = f"%{q.strip()}%"
                stmt = stmt.where(or_(SessionRow.title.ilike(like),
                                      SessionRow.summary.ilike(like)))
            total = int(db.scalar(select(func.count()).select_from(stmt.subquery())) or 0)
            # 契约排序：置顶优先，其次最近更新
            stmt = stmt.order_by(SessionRow.pinned.desc(), SessionRow.updated_at.desc())
            rows = db.scalars(stmt.limit(max(1, min(limit, 500))).offset(max(0, offset))).all()
            return [_row_to_dict(r) for r in rows], total
        finally:
            if own:
                db.close()

    def get(self, session_id: str, db=None) -> Optional[dict]:
        from db import SessionLocal
        own = db is None
        db = db or SessionLocal()
        try:
            row = db.get(SessionRow, session_id)
            if row is None:  # 旧版存档：先导入再取一次
                self._import_legacy(db)
                row = db.get(SessionRow, session_id)
            return _row_to_dict(row) if row else None
        finally:
            if own:
                db.close()

    def rename(self, session_id: str, title: str, db=None) -> Optional[dict]:
        return self._patch(session_id, db, title=((title or "")[:200] or None))

    def set_pinned(self, session_id: str, pinned: bool, db=None) -> Optional[dict]:
        return self._patch(session_id, db, pinned=bool(pinned))

    def _patch(self, session_id: str, db, **fields) -> Optional[dict]:
        """会话字段更新：updated_at 由服务层显式维护（契约要求列表按其倒序）。"""
        from db import SessionLocal
        own = db is None
        db = db or SessionLocal()
        try:
            row = db.get(SessionRow, session_id)
            if row is None:
                return None
            for k, v in fields.items():
                if v is not None:
                    setattr(row, k, v)
            row.updated_at = utcnow()
            db.commit()
            return _row_to_dict(row)
        finally:
            if own:
                db.close()

    def delete(self, session_id: str, db=None) -> bool:
        from db import SessionLocal
        own = db is None
        db = db or SessionLocal()
        try:
            row = db.get(SessionRow, session_id)
            if row is None:
                return False
            for model in (Message, TaskRow, ToolCall, TrajectoryNode):
                for r in db.scalars(select(model).where(model.session_id == session_id)).all():
                    db.delete(r)
            for r in db.scalars(select(Run).where(Run.session_id == session_id)).all():
                db.delete(r)
            db.delete(row)
            db.commit()
            self._delete_legacy_file(session_id)
            return True
        finally:
            if own:
                db.close()

    # ================= 历史聚合 =================
    def history(self, session_id: str, db=None) -> Optional[dict]:
        from db import SessionLocal
        own = db is None
        db = db or SessionLocal()
        try:
            self._import_legacy(db)
            row = db.get(SessionRow, session_id)
            if row is None:
                return None
            messages = db.scalars(select(Message).where(Message.session_id == session_id)
                                  .order_by(Message.ts.asc(), Message.id.asc())).all()
            tasks = db.scalars(select(TaskRow).where(TaskRow.session_id == session_id)
                               .order_by(TaskRow.seq.asc())).all()
            calls = db.scalars(select(ToolCall).where(ToolCall.session_id == session_id)
                               .order_by(ToolCall.created_at.asc())).all()
            nodes = db.scalars(select(TrajectoryNode)
                               .where(TrajectoryNode.session_id == session_id)
                               .order_by(TrajectoryNode.seq.asc())).all()
            return {
                "session": _row_to_dict(row),
                "messages": [{"id": m.id, "role": m.role, "content": m.content,
                              "ts": float(m.ts or 0.0), "run_id": m.run_id or "",
                              "tokens": dict(m.tokens or {})} for m in messages],
                "tasks": [self._task_dict(t) for t in tasks],
                "tool_calls": [self._call_dict(c) for c in calls],
                "trajectory": [self._node_dict(n) for n in nodes],
                "stats": self.aggregate_stats(session_id, db=db),
            }
        finally:
            if own:
                db.close()

    def aggregate_stats(self, session_id: str, db=None) -> dict:
        """会话级统计：以本会话最近一次 run 的 stats 为底（token/缓存/TPS 都在这儿），
        叠加会话自身的消息数与累计 LLM 调用，前端 Token 面板直接可用。"""
        from db import SessionLocal
        own = db is None
        db = db or SessionLocal()
        try:
            row = db.get(SessionRow, session_id)
            if row is None:
                return {}
            run = db.scalars(select(Run).where(Run.session_id == session_id)
                             .order_by(Run.started_at.desc())).first()
            stats: dict = {}
            if run is not None and isinstance(run.stats, dict):
                stats.update(run.stats)
            stats.update({k: v for k, v in dict(row.token_stats or {}).items()
                          if k not in stats})
            stats["message_count"] = int(row.message_count or 0)
            if run is not None:
                stats["last_run_id"] = run.id
                stats["last_run_status"] = run.status
            return stats
        finally:
            if own:
                db.close()

    # ================= 运行态搬运 =================
    def create_run(self, session_id: str, task: str = "", preset: str = "standard",
                   workspace: str = "default", permission_mode: str = "workspace_write",
                   model: str = "", run_id: str = "", db=None) -> dict:
        """登记一次运行。

        `run_id` 必须由调用方（RunService）传入它在事件里使用的那个 ID，
        否则登记行与 SSE 事件中的 run_id 对不上，`/api/agent/runs/{id}` 与
        收尾统计都会写进一条谁也不认识的孤立记录。
        """
        from db import SessionLocal
        own = db is None
        db = db or SessionLocal()
        try:
            rid = (run_id or "")[:32] or gen_id()
            run = db.get(Run, rid)
            if run is None:
                run = Run(id=rid, started_at=utcnow())
                db.add(run)
            run.session_id = session_id
            run.task = (task or "")[:20000]
            run.preset, run.workspace = preset, workspace
            run.permission_mode = permission_mode
            run.model = model or self._default_model()
            if run.status in ("", None):
                run.status = "queued"
            db.commit()
            return self._run_dict(run)
        finally:
            if own:
                db.close()

    def update_run(self, run_id: str, db=None, **fields) -> Optional[dict]:
        from db import SessionLocal
        own = db is None
        db = db or SessionLocal()
        try:
            run = db.get(Run, run_id)
            if run is None:
                return None
            for k, v in fields.items():
                if hasattr(run, k):
                    setattr(run, k, v)
            if fields.get("status") in ("done", "failed", "interrupted") and run.finished_at is None:
                run.finished_at = utcnow()
            db.commit()
            return self._run_dict(run)
        finally:
            if own:
                db.close()

    def get_run(self, run_id: str, db=None) -> Optional[dict]:
        from db import SessionLocal
        own = db is None
        db = db or SessionLocal()
        try:
            run = db.get(Run, run_id)
            return self._run_dict(run) if run else None
        finally:
            if own:
                db.close()

    def record_node(self, run_id: str, session_id: str, node: str, label: str = "",
                    seq: int = 0, status: str = "running", elapsed_ms: int = 0,
                    input: Optional[dict] = None, output: Optional[dict] = None,
                    tokens: Optional[dict] = None, error: str = "", db=None) -> dict:
        """写入/更新一个轨迹节点（同一 run+seq 视为同一节点，幂等覆盖）。"""
        from db import SessionLocal
        own = db is None
        db = db or SessionLocal()
        try:
            row = db.scalars(select(TrajectoryNode).where(
                TrajectoryNode.run_id == run_id, TrajectoryNode.seq == seq)).first()
            if row is None:
                row = TrajectoryNode(id=gen_id(), run_id=run_id, session_id=session_id,
                                     seq=seq, created_at=utcnow())
                db.add(row)
            row.node = node or row.node
            row.label = label or row.label
            row.status = status or row.status
            row.elapsed_ms = int(elapsed_ms or 0)
            if input is not None:
                row.input = _cap(input, 2000)
            if output is not None:
                row.output = _cap(output, 2000)
            if tokens is not None:
                row.tokens = dict(tokens or {})
            row.error = error[:2000] if error else ""
            db.commit()
            return self._node_dict(row)
        finally:
            if own:
                db.close()

    def record_tool_call(self, run_id: str, session_id: str, task_id: str, tool: str,
                         args: Optional[dict] = None, brief: str = "", ok: bool = True,
                         error: str = "", elapsed_ms: int = 0, cached: bool = False,
                         tokens: Optional[dict] = None, call_id: str = "", db=None) -> dict:
        """写入一次工具调用记录。

        `call_id` 存在时用作主键：SSE 的 tool_call（开始）与 tool_result/tool_error（结束）
        共享同一个 call_id，两次写入自然合并成一行，不必再维护额外的映射表。
        """
        from db import SessionLocal
        own = db is None
        db = db or SessionLocal()
        try:
            rid = (call_id or "")[:32] or gen_id()
            row = db.get(ToolCall, rid) if call_id else None
            if row is None:
                row = ToolCall(id=rid, created_at=utcnow())
                db.add(row)
            row.run_id, row.session_id, row.task_id = run_id, session_id, task_id
            row.tool = tool
            if args:
                row.args = _cap(args, 2000)
            row.brief = (brief or row.brief or "")[:4000]
            row.ok = bool(ok)
            row.error = (error or "")[:2000]
            row.elapsed_ms = int(elapsed_ms or row.elapsed_ms or 0)
            row.cached = bool(cached or row.cached)
            if tokens:
                row.tokens = dict(tokens)
            db.commit()
            return self._call_dict(row)
        finally:
            if own:
                db.close()

    def update_tool_call(self, call_id: str, db=None, **fields) -> Optional[dict]:
        """按 call_id 回填工具调用结果（brief/ok/error/elapsed_ms/cached/tokens）。"""
        if not call_id:
            return None
        from db import SessionLocal
        own = db is None
        db = db or SessionLocal()
        try:
            row = db.get(ToolCall, call_id[:32])
            if row is None:
                return None
            for k in ("brief", "ok", "error", "elapsed_ms", "cached", "tokens"):
                if k in fields and fields[k] is not None:
                    setattr(row, k, fields[k])
            db.commit()
            return self._call_dict(row)
        finally:
            if own:
                db.close()

    def reconcile_runs(self, stale_seconds: int = 300, db=None) -> int:
        """进程重启时清理僵尸运行：把长时间停留在 queued/running 的 run 标记为 interrupted。

        为什么需要：服务被强杀时收尾任务来不及执行，这些行会永远显示「排队中」，
        前端每次加载历史都会看到一条永不结束的运行。
        """
        from db import SessionLocal
        own = db is None
        db = db or SessionLocal()
        try:
            cutoff = utcnow().timestamp() - max(30, int(stale_seconds))
            rows = db.scalars(select(Run).where(Run.status.in_(("queued", "running")))).all()
            n = 0
            for run in rows:
                started = run.started_at
                if started is None:
                    continue
                if started.tzinfo is None:
                    started = started.replace(tzinfo=timezone.utc)
                if started.timestamp() > cutoff:
                    continue
                run.status = "interrupted"
                run.error = run.error or "服务重启，运行未完成即中断"
                run.finished_at = utcnow()
                n += 1
            if n:
                db.commit()
                print(f"[session] 已归档 {n} 条中断的残留运行")
            return n
        finally:
            if own:
                db.close()

    def save_runtime(self, session, run_id: str = "", db=None) -> None:
        """JobAgent 的 Session → 落库（消息 / 任务 / 画像 / token 统计）。

        幂等：先清空该会话的 messages / tasks 再整批写入，重复调用不会产生重复行。
        """
        from db import SessionLocal
        own = db is None
        db = db or SessionLocal()
        try:
            row = db.get(SessionRow, session.id)
            if row is None:
                row = SessionRow(id=session.id, created_at=self._created_at(session))
                db.add(row)
            runtime = {
                "artifacts": _cap(getattr(session, "artifacts", {}) or {}),
                "pending_question": getattr(session, "pending_question", "") or "",
                "waiting_task_id": getattr(session, "waiting_task_id", "") or "",
                "event_log": _cap((getattr(session, "event_log", []) or [])[-MAX_EVENT_LOG:]),
                "llm_calls": int(getattr(session, "llm_calls", 0) or 0),
                "created_at": float(getattr(session, "created_at", 0) or 0),
            }
            facts = {k: v for k, v in (session.facts or {}).items() if k != RUNTIME_KEY}
            row.facts = {**facts, RUNTIME_KEY: runtime}
            row.title = (getattr(session, "title", "") or row.title or "新会话")[:200]
            row.status = getattr(session, "status", "") or "idle"
            row.summary = getattr(session, "summary", "") or ""
            row.message_count = len(session.messages or [])
            row.token_stats = self._merge_token_stats(row.token_stats, session)
            row.updated_at = utcnow()

            for m in db.scalars(select(Message).where(Message.session_id == session.id)).all():
                db.delete(m)
            for t in db.scalars(select(TaskRow).where(TaskRow.session_id == session.id)).all():
                db.delete(t)
            db.flush()
            for m in (session.messages or []):
                db.add(Message(session_id=session.id, run_id=run_id,
                               role=str(m.get("role") or "user"),
                               content=str(m.get("content") or ""),
                               ts=float(m.get("ts") or time.time()),
                               tokens=dict(m.get("tokens") or {})))
            for i, t in enumerate(session.tasks or []):
                db.add(self._task_row(session.id, run_id, i, t))
            db.commit()
        finally:
            if own:
                db.close()

    def load_runtime(self, session_id: str, db=None):
        """落库数据 → core.memory.Session（供 RunService/图编排续跑）。"""
        from core.memory import Session, Task
        data = self.history(session_id, db)
        if data is None:
            return None
        s = Session(id=session_id)
        facts = dict(data["session"].get("facts") or {})
        runtime = facts.pop(RUNTIME_KEY, {}) or {}
        s.title = data["session"].get("title") or "新会话"
        s.status = data["session"].get("status") or "idle"
        s.facts = facts
        s.summary = data["session"].get("summary") or ""
        s.artifacts = dict(runtime.get("artifacts") or {})
        s.pending_question = runtime.get("pending_question", "")
        s.waiting_task_id = runtime.get("waiting_task_id", "")
        s.event_log = list(runtime.get("event_log") or [])
        s.llm_calls = int(runtime.get("llm_calls") or 0)
        created = runtime.get("created_at")
        s.created_at = float(created) if created else time.time()
        s.messages = [{"role": m["role"], "content": m["content"], "ts": m["ts"]}
                      for m in data["messages"]]
        # 任务 ID 还原为业务侧原始值（落库主键是「会话ID:序号」，见 _task_row 说明）
        s.tasks = [Task(**{k: v for k, v in t.items()
                           if k in Task.__dataclass_fields__}) for t in data["tasks"]]
        return s

    # ================= 兼容旧版存档 =================
    def import_legacy(self, db=None) -> int:
        """把 data/sessions/*.json 旧存档导入数据库（进程内只做一次）。"""
        global _legacy_imported
        if _legacy_imported:
            return 0
        with _import_lock:
            if _legacy_imported:
                return 0
            n = self._import_legacy(db)
            _legacy_imported = True
            return n

    def _import_legacy(self, db) -> int:
        from db import SessionLocal
        from core import secure_store
        own = db is None
        db = db or SessionLocal()
        count = 0
        try:
            sessions_dir = Path(getattr(self.cfg, "sessions_dir", Path("data/sessions")))
            if not sessions_dir.exists():
                return 0
            for path in sorted(sessions_dir.glob("*.json")):
                sid = path.stem
                if db.get(SessionRow, sid) is not None:
                    continue
                try:
                    raw = secure_store.read_bytes(path).decode("utf-8", "ignore")
                    data = json.loads(raw)
                except Exception:
                    continue  # 解开不了/格式不对的存档跳过，绝不能拖垮列表接口
                if not isinstance(data, dict) or not data.get("id"):
                    continue
                ts = float(data.get("created_at") or 0) or time.time()
                runtime = {
                    "artifacts": _cap(data.get("artifacts") or {}),
                    "pending_question": data.get("pending_question", ""),
                    "waiting_task_id": data.get("waiting_task_id", ""),
                    "event_log": _cap((data.get("event_log") or [])[-MAX_EVENT_LOG:]),
                    "llm_calls": int(data.get("llm_calls") or 0),
                    "created_at": ts,
                }
                facts = {k: v for k, v in (data.get("facts") or {}).items()
                         if k != RUNTIME_KEY}
                row = SessionRow(
                    id=sid, title=(data.get("title") or "新会话")[:200],
                    status=data.get("status") or "idle", workspace="default",
                    preset="standard", permission_mode="workspace_write",
                    model=self._default_model(), pinned=False,
                    facts={**facts, RUNTIME_KEY: runtime},
                    summary=data.get("summary") or "",
                    token_stats={}, message_count=len(data.get("messages") or []),
                    created_at=datetime.fromtimestamp(ts, timezone.utc),
                    updated_at=datetime.fromtimestamp(ts, timezone.utc))
                db.add(row)
                for m in (data.get("messages") or []):
                    db.add(Message(session_id=sid, run_id="",
                                   role=str(m.get("role") or "user"),
                                   content=str(m.get("content") or ""),
                                   ts=float(m.get("ts") or ts), tokens={}))
                for i, t in enumerate(data.get("tasks") or []):
                    try:
                        from core.memory import Task as CoreTask
                        db.add(self._task_row(sid, "", i, CoreTask.from_dict(t)))
                    except Exception:
                        continue
                db.commit()
                count += 1
        finally:
            if own:
                db.close()
        if count:
            print(f"[session] 已导入 {count} 个旧版会话存档")
        return count

    # ================= 内部工具 =================
    def _default_model(self) -> str:
        cfg = self.cfg
        return getattr(cfg, "model", "") or ((getattr(cfg, "models", []) or [""])[0])

    @staticmethod
    def _created_at(session) -> datetime:
        ts = float(getattr(session, "created_at", 0) or 0) or time.time()
        return datetime.fromtimestamp(ts, timezone.utc)

    @staticmethod
    def _merge_token_stats(current: Optional[dict], session) -> dict:
        """会话级 token 统计：保留已有累计值，消息数/LLM 调用数实时覆盖。"""
        stats = dict(current or {})
        stats["message_count"] = len(getattr(session, "messages", []) or [])
        stats["llm_calls"] = int(getattr(session, "llm_calls", 0) or 0)
        stats["updated_at"] = time.time()
        return stats

    @staticmethod
    def _task_row(session_id: str, run_id: str, seq: int, t) -> TaskRow:
        """core.memory.Task → tasks 行；契约只有 tokens 一个 JSON 列，规划信息藏其保留键。

        主键坑：业务侧任务 ID 形如 `t1`/`t2`，**跨会话必然重复**，直接当主键会撞唯一约束。
        因此落库主键统一用「会话ID:序号」，原始 ID 存进 plan.orig_id，读回时再还原。
        """
        steps = list(getattr(t, "steps", []) or [])
        orig = str(getattr(t, "id", "") or "")
        return TaskRow(
            id=f"{session_id}:{seq}"[:32], run_id=run_id,
            session_id=session_id, seq=seq, title=str(getattr(t, "title", ""))[:300],
            detail=str(getattr(t, "detail", "") or ""), tool=str(getattr(t, "tool", "none")),
            status=str(getattr(t, "status", "pending")),
            result=str(getattr(t, "result", "") or ""), error=str(getattr(t, "error", "") or ""),
            retries=int(getattr(t, "retries", 0) or 0), elapsed_ms=0,
            tokens={"plan": _cap({"args": getattr(t, "args", {}) or {},
                                  "depends_on": getattr(t, "depends_on", []) or [],
                                  "condition": getattr(t, "condition", {}) or {},
                                  "orig_id": orig}, 1000),
                    "steps": _cap(steps, 1200)})

    @staticmethod
    def _task_dict(t: TaskRow) -> dict:
        tokens = dict(t.tokens or {})
        plan = tokens.get("plan") or {}
        return {"id": plan.get("orig_id") or t.id, "storage_id": t.id,
                "run_id": t.run_id, "seq": t.seq, "title": t.title,
                "detail": t.detail, "tool": t.tool, "status": t.status,
                "result": t.result, "error": t.error, "retries": t.retries,
                "elapsed_ms": t.elapsed_ms, "args": plan.get("args") or {},
                "depends_on": plan.get("depends_on") or [],
                "condition": plan.get("condition") or {},
                "steps": tokens.get("steps") or []}

    @staticmethod
    def _call_dict(c: ToolCall) -> dict:
        return {"id": c.id, "run_id": c.run_id, "session_id": c.session_id,
                "task_id": c.task_id, "tool": c.tool, "args": dict(c.args or {}),
                "brief": c.brief, "ok": bool(c.ok), "error": c.error,
                "elapsed_ms": c.elapsed_ms, "cached": bool(c.cached),
                "tokens": dict(c.tokens or {}), "created_at": iso(c.created_at)}

    @staticmethod
    def _node_dict(n: TrajectoryNode) -> dict:
        return {"id": n.id, "run_id": n.run_id, "session_id": n.session_id, "seq": n.seq,
                "node": n.node, "label": n.label, "status": n.status,
                "elapsed_ms": n.elapsed_ms, "input": dict(n.input or {}),
                "output": dict(n.output or {}), "tokens": dict(n.tokens or {}),
                "error": n.error, "created_at": iso(n.created_at),
                "node_id": f"{n.run_id}:{n.seq}"}

    @staticmethod
    def _run_dict(r: Run) -> dict:
        return {"run_id": r.id, "session_id": r.session_id, "status": r.status,
                "task": r.task, "preset": r.preset, "workspace": r.workspace,
                "permission_mode": r.permission_mode, "model": r.model,
                "started_at": iso(r.started_at), "finished_at": iso(r.finished_at),
                "stats": dict(r.stats or {}), "error": r.error}

    @staticmethod
    def _delete_legacy_file(session_id: str) -> None:
        try:
            from config import load_config as _lc
            path = Path(_lc().sessions_dir) / f"{session_id}.json"
            if path.exists():
                path.unlink()
        except OSError:
            pass


__all__ = ["SessionService", "RUNTIME_KEY"]
