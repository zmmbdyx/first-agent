"""RunService：把一次「用户任务」变成契约（docs/ARCHITECTURE.md 2.2）定义的 SSE 事件流。

职责边界（刻意的窄）：
- **会话解析与落库**交给 `SessionService`（SQLAlchemy）；不存在的会话在这里按需新建；
- **编排本身**交给 `core.graph.astream_run`（LangGraph 状态机 + 既有 JobAgent 业务逻辑）；
- 本层只做四件事：准入排队、事件双通道转发（SSE 主链路 + WebSocket 补充通道）、
  运行收尾持久化、中断/回滚这类控制面操作。

为什么不在这里重写业务：任务规划、ReAct 步进、工具重试熔断、产物接线等规则都已存在于
`core/agent.py`，编排层只负责"按什么顺序调用"和"怎么观测"，复制一份必然与主逻辑漂移。
"""
from __future__ import annotations

import asyncio
import threading
import time
import uuid
from collections import OrderedDict
from typing import Any, AsyncIterator, Dict, Optional

from config import Settings, load_config
from core.agent import JobAgent
from core.checkpointer import SnapshotStore
from core.graph import astream_run
from core.memory import Session
from core.presets import apply_preset

import observability as obs

from services.broadcast import broadcast

# 单进程内同时在跑的 run 数上限（超出即排队）；可用 MAX_CONCURRENT_RUNS 调整
DEFAULT_CONCURRENCY = 2
# 每会话独立 JobAgent 实例的缓存上限（实例态含工具缓存/统计，复用可省重复分析）
AGENT_POOL_CAP = 32
# 中断后等待编排收尾的最长时间：太短会丢落库，太长会让"停止"按钮看起来没反应
INTERRUPT_GRACE = 5.0

# 推理强度 → 采样温度/步数的映射（前端"推理强度"开关的实际作用点）
EFFORT_TUNING = {
    "low": {"temperature_scale": 0.5, "step_delta": -2},
    "medium": {"temperature_scale": 1.0, "step_delta": 0},
    "high": {"temperature_scale": 1.4, "step_delta": 2},
}


class RunServiceError(RuntimeError):
    """调用方可见的错误（API 层据此映射状态码）。"""


class QueueFullError(RunServiceError):
    """准入队列已满（API 层映射为 429）。"""


class _Admission:
    """优先级准入闸门：小优先级先执行，同优先级按到达顺序（FIFO）。

    为什么不用纯 asyncio.Semaphore：Semaphore 是 FIFO 的，无法表达优先级；
    这里的等待队列显式排序，且 release 时重新挑选最优先的等待者。
    队列有上限（`max_queue`）：无界队列在接口被刷时会把内存和 Future 一起堆爆，
    超限时快速失败（429）比"排到天荒地老"更诚实。
    """

    def __init__(self, limit: int = DEFAULT_CONCURRENCY, max_queue: int = 32) -> None:
        self.limit = max(1, int(limit or DEFAULT_CONCURRENCY))
        self.max_queue = max(1, int(max_queue or 32))
        self._active: Dict[str, int] = {}
        self._waiting: list[tuple[int, int, str, asyncio.Future]] = []
        self._seq = 0
        self._lock = asyncio.Lock()

    @property
    def active_count(self) -> int:
        return len(self._active)

    @property
    def waiting_count(self) -> int:
        return len(self._waiting)

    def capacity_error(self) -> str:
        """未超限返回空串；超限返回可读原因（供 API 提前 429 与流内兜底共用）。"""
        if self.waiting_count >= self.max_queue:
            return (f"执行队列已满（等待 {self.waiting_count}/{self.max_queue}，"
                    f"并发上限 {self.limit}），请稍后重试")
        return ""

    async def acquire(self, run_id: str, priority: int = 5) -> None:
        async with self._lock:
            if len(self._active) < self.limit:
                self._active[run_id] = max(0, min(int(priority or 5), 9))
                return
            if len(self._waiting) >= self.max_queue:
                raise QueueFullError(self.capacity_error() or "执行队列已满")
            self._seq += 1
            fut: asyncio.Future = asyncio.get_running_loop().create_future()
            self._waiting.append((max(0, min(int(priority or 5), 9)), self._seq, run_id, fut))
        await fut  # 被 _release 唤醒时已占好名额

    def _release_locked(self) -> None:
        if not self._waiting or len(self._active) >= self.limit:
            return
        self._waiting.sort(key=lambda item: (item[0], item[1]))
        priority, _seq, run_id, fut = self._waiting.pop(0)
        self._active[run_id] = priority
        if not fut.done():
            fut.set_result(True)

    def release(self, run_id: str) -> None:
        self._active.pop(run_id, None)
        self._release_locked()

    def cancel(self, run_id: str) -> bool:
        """取消仍在排队的 run（已经开跑的不在这里处理）。"""
        for i, item in enumerate(self._waiting):
            if item[2] == run_id:
                self._waiting.pop(i)
                if not item[3].done():
                    item[3].cancel()
                return True
        return False

    def position(self, run_id: str) -> int:
        """排队位次（0 = 已获准执行）。"""
        if run_id in self._active:
            return 0
        ordered = sorted(self._waiting, key=lambda item: (item[0], item[1]))
        for idx, item in enumerate(ordered, start=1):
            if item[2] == run_id:
                return idx
        return -1

    def snapshot(self) -> dict:
        return {"active": len(self._active), "waiting": len(self._waiting), "limit": self.limit}


def _pick(payload: Any, key: str, default: Any = None) -> Any:
    """同时兼容 pydantic 模型与普通 dict 的请求体（API 层传的是 RunRequest）。"""
    if payload is None:
        return default
    if isinstance(payload, dict):
        return payload.get(key, default)
    return getattr(payload, key, default)


class RunService:
    """SSE 事件流的数据源。线程安全：准入闸门与 Agent 池都加了锁/事件循环内串行。"""

    def __init__(self, cfg: Optional[Settings] = None, session_service: Any = None,
                 tool_service: Any = None, queue: Any = None, broadcaster: Any = None) -> None:
        self.cfg = cfg or load_config()
        self.sessions = session_service
        self.tools = tool_service
        self.queue = queue                      # 可选的 Redis/进程内优先级队列（用于取消）
        self._broadcast = broadcaster or broadcast
        self._runs: Dict[str, Dict[str, Any]] = {}
        self._active_tasks: Dict[str, asyncio.Task] = {}
        self._bg_tasks: set = set()             # 游离的收尾任务（防止被 GC）
        self._session_owner: Dict[str, str] = {}
        self._agents: "OrderedDict[str, JobAgent]" = OrderedDict()
        self._agent_lock = threading.Lock()
        self._lock = threading.Lock()
        limit = int(getattr(self.cfg, "max_concurrent_runs", DEFAULT_CONCURRENCY) or DEFAULT_CONCURRENCY)
        max_queue = int(getattr(self.cfg, "max_queue_size", 32) or 32)
        self._admission = _Admission(limit, max_queue)
        self._snapshots = SnapshotStore(getattr(self.cfg, "sessions_dir", None))

    # ================= 控制面 =================
    def precheck(self, req: Any) -> None:
        """流开始前的快速失败检查（API 层在建立 SSE 之前调用）。

        为什么需要单独一步：SSE 一旦开始输出就无法再改 HTTP 状态码，
        队列满必须在此之前以 429 拒绝，而不是"先 200 再发一条错误事件"。
        """
        capacity = self._admission.capacity_error()
        if capacity:
            raise QueueFullError(capacity)
        if not str(_pick(req, "task", "") or "").strip():
            raise RunServiceError("task 不能为空")

    def interrupt(self, run_id: str) -> bool:
        """中断执行：排队中的直接出队；已在跑的取消其 asyncio 任务并落库为 interrupted。"""
        if self._admission.cancel(run_id):
            self._mark(run_id, status="interrupted", error="用户中断（排队中）")
            self._broadcast_status(run_id, "interrupted", "排队中已被取消")
            return True

        task = self._active_tasks.get(run_id)
        if task is not None and not task.done():
            task.cancel()
            self._mark(run_id, status="interrupted", error="用户中断")
            self._broadcast_status(run_id, "interrupted", "执行已中断")
            return True

        if self.queue is not None and hasattr(self.queue, "cancel"):
            try:
                if self.queue.cancel(run_id):
                    self._mark(run_id, status="interrupted", error="用户中断（队列）")
                    return True
            except Exception:
                pass
        return False

    def rollback(self, session_id: str, run_id: str = "", checkpoint_id: str = "") -> dict:
        """把会话状态回滚到某个检查点（空 checkpoint_id 即回滚到最近一次）。

        回滚的是"会话态"（消息/任务/产物），不回滚工作区里已经落盘的文件——
        文件是用户的真实产物，静默删除比回滚本身更危险。
        """
        snapshot = self._snapshots.load(session_id, checkpoint_id)
        if not snapshot:
            raise RunServiceError("没有可用的检查点（该会话尚未执行过，或检查点已被清理）")
        restored = self._snapshots.restore_session(session_id, checkpoint_id, Session)
        if restored is None:
            raise RunServiceError("检查点损坏，无法恢复会话状态")
        try:
            self.sessions.save_runtime(restored, run_id)
        except Exception as e:  # 落库失败也要让用户知道回滚没生效
            raise RunServiceError(f"回滚后写入数据库失败: {e}") from e
        cid = checkpoint_id or str(snapshot.get("checkpoint_id") or "")
        self._broadcast(session_id, {"type": "rolled_back", "checkpoint_id": cid})
        return {"ok": True, "session_id": session_id, "checkpoint_id": cid}

    def status(self, run_id: str) -> dict:
        meta = self._runs.get(run_id)
        if meta is not None:
            return dict(meta)
        if self.sessions is not None:
            row = self.sessions.get_run(run_id)
            if row:
                return row
        raise RunServiceError(f"未知的 run: {run_id}")

    def stats(self) -> dict:
        return {"runs": len(self._runs), "agents": len(self._agents),
                "admission": self._admission.snapshot()}

    # ================= 数据面（SSE 数据源） =================
    async def stream(self, req: Any, owner: Optional[str] = None) -> AsyncIterator[dict]:
        """驱动一次运行并逐条产出契约事件。调用方（api/agent.py）负责包装成 SSE 帧。

        `owner` 是**认证归属者**（多租户隔离维度），与下面用于"会话占用"的局部变量
        `holder` 不是一回事，故刻意不同名，避免后续维护者混淆两者。
        """
        task_text = str(_pick(req, "task", "") or "").strip()
        if not task_text:
            yield {"type": "error", "message": "task 不能为空"}
            return

        preset_id = str(_pick(req, "preset", "standard") or "standard")
        workspace = str(_pick(req, "workspace", "default") or "default")
        permission = str(_pick(req, "permission_mode", "workspace_write") or "workspace_write")
        model = str(_pick(req, "model", "") or "")
        resume = bool(_pick(req, "resume", False))
        priority = int(_pick(req, "priority", 5) or 5)
        effort = str(_pick(req, "reasoning_effort", "medium") or "medium")

        run_id = uuid.uuid4().hex[:12]
        acquired = False
        try:
            session = await asyncio.to_thread(self._resolve_session, req, workspace, preset_id, owner)
        except Exception as e:
            yield {"type": "error", "message": f"会话初始化失败: {e}"}
            return
        session_id = session.id

        # 同一会话不允许并发执行：JobAgent 是实例态的（工具缓存/统计/当前会话），
        # 并发跑会把事件与会话状态互相污染。
        with self._lock:
            holder = self._session_owner.get(session_id)
            if holder:
                yield {"type": "error",
                       "message": f"该会话正在执行中（run {holder}），请等待完成或先中断"}
                return
            self._session_owner[session_id] = run_id
            self._runs[run_id] = {
                "run_id": run_id, "session_id": session_id, "status": "queued",
                "owner": owner or "default", "prompt_version": str(getattr(self.cfg, "prompt_version", "")),
                "task": task_text[:2000], "preset": preset_id, "workspace": workspace,
                "permission_mode": permission, "model": model or getattr(self.cfg, "model", ""),
                "started_at": time.time(), "finished_at": None, "stats": {}, "error": "",
                "_nodes": 0, "_tools": 0, "_tokens": 0,
            }

        try:
            # 用本服务生成的 run_id 登记：事件流、控制面与 runs 表三处共用同一 id，
            # 否则按 run_id 回写状态会找不到行（实测表现为 run 永远停在 queued）
            await asyncio.to_thread(self._register_run, run_id, session, task_text, preset_id,
                                    workspace, permission, model, owner)

            obs.inc("pathforge_runs_started_total", preset=preset_id, owner=owner or "default")
            yield {"type": "run_started", "run_id": run_id, "session_id": session_id,
                   "model": model or getattr(self.cfg, "model", "") or "mock", "preset": preset_id,
                   "permission_mode": permission, "workspace": workspace}
            yield {"type": "session_info", "session_id": session_id,
                   "status": getattr(session, "status", "idle"), "title": getattr(session, "title", "")}

            # 排队位次：position 为 -1 表示尚未入队（并发未满，立即执行）
            pos = self._admission.position(run_id)
            yield {"type": "queue_position", "run_id": run_id,
                   "position": pos if pos > 0 else 0, "priority": priority}
            obs.gauge("pathforge_queue_waiting", self._admission.waiting_count)
            obs.gauge("pathforge_runs_active", self._admission.active_count)

            current = asyncio.current_task()
            if current is not None:
                self._active_tasks[run_id] = current
            await self._admission.acquire(run_id, priority)
            acquired = True
            self._mark(run_id, status="running")
            obs.gauge("pathforge_runs_active", self._admission.active_count)

            cfg = self._tuned_cfg(preset_id, effort)
            agent = await asyncio.to_thread(self._agent_for, session_id)
            self._save_start_snapshot(session_id, run_id, session)

            # 运行级墙钟超时（评审 P1-1）：此前 TASK_TIMEOUT 只作用于沙箱单命令，
            # 模型端点卡住时一次运行可以无限期占用并发额度并持续计费。
            budget_hit = ""
            try:
                async with asyncio.timeout(max(1, int(getattr(cfg, "task_timeout", 300) or 300))):
                    async for event in astream_run(agent, session, task_text, cfg,
                                                   run_id=run_id, preset=preset_id,
                                                   permission_mode=permission, workspace=workspace,
                                                   model=model, resume=resume, priority=priority,
                                                   snapshot_store=self._snapshots):
                        if not isinstance(event, dict):
                            continue
                        budget_hit = self._observe(run_id, session_id, event)
                        yield event
                        if budget_hit:
                            # 超预算：立刻停止继续驱动图（不再产生新的 LLM/工具调用），
                            # 已产出内容照常落库，状态置 interrupted
                            break
            except TimeoutError:
                budget_hit = f"运行超时（超过 {getattr(cfg, 'task_timeout', 300)}s）"
                obs.inc("pathforge_runs_timeout_total", owner=owner or "default")

            if budget_hit:
                self._mark(run_id, status="interrupted", error=budget_hit, finished_at=time.time())
                await self._finish(run_id, session, error=budget_hit, interrupted=True)
                yield {"type": "error", "message": budget_hit}
                yield {"type": "interrupted", "run_id": run_id, "reason": budget_hit}
                return

            await self._finish(run_id, session, error="", interrupted=False)
        except asyncio.CancelledError:
            # 中断路径：本协程所属的消费端任务已被 cancel，此刻**不能再 yield**
            # （在取消展开过程中让生成器挂起，会把任务卡在"已取消但未结束"的状态）。
            # 因此把收尾落库交给一个游离任务，收尾完成后由 WebSocket 通道告知前端。
            obs.inc("pathforge_runs_interrupted_total", owner=owner or "default")
            self._mark(run_id, status="interrupted", error="用户中断", finished_at=time.time())
            self._spawn_finish(run_id, session, "用户中断", True)
            raise
        except QueueFullError as e:
            # 队列在 acquire 时被占满（precheck 之后的竞态窗口）
            obs.inc("pathforge_queue_rejected_total")
            self._mark(run_id, status="failed", error=str(e), finished_at=time.time())
            await self._finish(run_id, session, error=str(e), interrupted=False)
            yield {"type": "error", "message": str(e)}
        except Exception as e:
            obs.inc("pathforge_runs_failed_total", owner=owner or "default")
            await self._finish(run_id, session, error=f"{type(e).__name__}: {e}",
                               interrupted=False)
            yield {"type": "error", "message": f"{type(e).__name__}: {e}"}
        finally:
            if acquired:
                self._admission.release(run_id)
            self._active_tasks.pop(run_id, None)
            obs.gauge("pathforge_runs_active", self._admission.active_count)
            obs.gauge("pathforge_queue_waiting", self._admission.waiting_count)
            with self._lock:
                if self._session_owner.get(session_id) == run_id:
                    self._session_owner.pop(session_id, None)

    # ================= 内部：会话与 Agent =================
    def _resolve_session(self, req: Any, workspace: str, preset_id: str,
                         owner: Optional[str] = None) -> Session:
        """取已存在的会话；不存在（或未指定）则新建一条并返回可运行的 Session 对象。

        `owner` 非空时，只有属于该 owner 的会话能被续跑；别人的 session_id 会被
        当作"不存在"从而新建一条——避免通过 ID 猜测读写他人会话。
        """
        session_id = str(_pick(req, "session_id", "") or "").strip()
        session: Optional[Session] = None
        if session_id and self.sessions is not None:
            session = self.sessions.load_runtime(session_id, owner=owner)
        if session is None:
            if self.sessions is not None:
                title = str(_pick(req, "task", "") or "").strip()[:30] or "新会话"
                row = self.sessions.create(title=title, workspace=workspace, preset=preset_id,
                                           owner=owner or "default")
                session = self.sessions.load_runtime(row["id"], owner=owner)
                if session is None:
                    session = Session(id=row["id"], title=title)
            else:  # 没有会话服务时退化为"纯内存运行"，保证端点仍可用
                session = Session(id=session_id or uuid.uuid4().hex[:12])
        return session

    def _agent_for(self, session_id: str) -> JobAgent:
        """按会话取 JobAgent（复用工具缓存/统计）。同步方法，调用方负责丢线程池。"""
        with self._agent_lock:
            agent = self._agents.get(session_id)
            if agent is None:
                agent = JobAgent(self.cfg)
                registry = self._custom_registry()
                if registry is not None:
                    # 复用 ToolService 的注册表：用户自定义（HTTP/MCP）工具立即可见
                    agent.registry = registry
                self._agents[session_id] = agent
                if len(self._agents) > AGENT_POOL_CAP:
                    self._agents.popitem(last=False)
            else:
                self._agents.move_to_end(session_id)
            return agent

    def _custom_registry(self):
        getter = getattr(self.tools, "registry", None)
        if not callable(getter):
            return None
        try:
            return getter()
        except Exception:
            return None

    def _tuned_cfg(self, preset_id: str, effort: str) -> Settings:
        """预设 + 推理强度 → 一份运行期配置副本（不改全局配置对象）。"""
        cfg = apply_preset(self.cfg, preset_id)
        tuning = EFFORT_TUNING.get(effort, EFFORT_TUNING["medium"])
        try:
            temperature = max(0.0, min(1.5, float(cfg.temperature) * tuning["temperature_scale"]))
        except (TypeError, ValueError):
            temperature = cfg.temperature
        steps = max(1, int(cfg.max_react_steps) + int(tuning["step_delta"]))
        return cfg.model_copy(update={"temperature": round(temperature, 3),
                                      "max_react_steps": steps,
                                      "preset_id": preset_id,
                                      "reasoning_effort": effort})

    # ================= 内部：登记 / 观测 / 收尾 =================
    def _register_run(self, run_id: str, session: Session, task_text: str, preset_id: str,
                      workspace: str, permission: str, model: str,
                      owner: Optional[str] = None) -> None:
        if self.sessions is None:
            return
        try:
            # 传入本服务生成的 run_id：事件流里的 run_id 与库中登记行必须是同一个，
            # 否则 /api/agent/runs/{id} 与收尾统计会落到一条无人认领的孤立记录上
            self.sessions.create_run(session.id, task=task_text, preset=preset_id,
                                     workspace=workspace, permission_mode=permission,
                                     model=model, run_id=run_id,
                                     owner=owner or "default",
                                     prompt_version=str(getattr(self.cfg, "prompt_version", "")))
        except Exception as e:
            print(f"[run_service] 运行登记失败（不阻断执行）: {type(e).__name__}: {e}")

    def _save_start_snapshot(self, session_id: str, run_id: str, session: Session) -> None:
        """run 起点快照：回滚的兜底锚点（图内部还会按节点写更细的检查点）。"""
        try:
            # 契约：SnapshotStore.save(session_id, checkpoint_id, session, meta)；
            # 早先的写法把 run_id 当 checkpoint_id 且把 label 传成了顶层关键字，
            # 结果是每次运行都抛 TypeError、快照根本没落盘，回滚必然“没有可用的检查点”。
            self._snapshots.save(session_id, f"run:{run_id}", session,
                                 meta={"label": "run_start", "run_id": run_id})
        except Exception as e:
            print(f"[run_service] 起点快照写入失败（不影响执行）: {type(e).__name__}: {e}")

    def _observe(self, run_id: str, session_id: str, event: dict) -> str:
        """把事件同步到控制面状态，并把需要实时推送的类型转发到 WebSocket 通道。

        返回非空字符串表示**触发了 token 预算**（调用方据此中止本次运行）。

        轨迹节点数与工具调用次数在这里**由事件流自行计数**：agent._finalize 产出的
        stats 里没有 `nodes` 字段，直接透传会让界面上的「本节点数」永远是 0。
        """
        etype = str(event.get("type") or "")
        if etype in ("node_start", "tool_call"):
            with self._lock:
                meta = self._runs.get(run_id)
                if meta is not None:
                    meta["_nodes" if etype == "node_start" else "_tools"] = \
                        int(meta.get("_nodes" if etype == "node_start" else "_tools") or 0) + 1
            if etype == "tool_call":
                obs.inc("pathforge_tool_calls_total", tool=str(event.get("tool") or "unknown"))
        if etype == "metric":
            self._mark(run_id, stats={k: v for k, v in event.items() if k != "type"})
            self._track_tokens(run_id, int(event.get("total_tokens") or 0))
            obs.gauge("pathforge_context_ratio", float(event.get("context_ratio") or 0.0))
            obs.gauge("pathforge_cache_hit_rate", float(event.get("cache_hit_rate") or 0.0))
        elif etype in ("run_done", "final_answer") and isinstance(event.get("stats"), dict):
            self._mark(run_id, stats=event["stats"])
        elif etype == "ask_user":
            self._mark(run_id, status="awaiting_input")
        if etype in ("tool_status", "tool_call", "tool_result", "tool_error", "metric",
                     "trajectory", "node_start", "node_end", "task_finish", "interrupted"):
            self._broadcast(session_id, event)
        return self._budget_error(run_id)

    def _track_tokens(self, run_id: str, total_tokens: int) -> None:
        """按 metric 事件的**累计值**记录本 run 的 token 用量（取最大值，容忍乱序）。"""
        with self._lock:
            meta = self._runs.get(run_id)
            if meta is not None:
                meta["_tokens"] = max(int(meta.get("_tokens") or 0), int(total_tokens))
                obs.gauge("pathforge_run_tokens", meta["_tokens"])

    def _budget_error(self, run_id: str) -> str:
        """token 预算判定：超限返回可读原因，否则空串。0 表示不限制。"""
        limit = int(getattr(self.cfg, "max_tokens_per_run", 0) or 0)
        if limit <= 0:
            return ""
        used = int((self._runs.get(run_id) or {}).get("_tokens") or 0)
        if used > limit:
            return f"超出 token 预算（已用 {used}，上限 {limit}）"
        return ""

    def _broadcast_status(self, run_id: str, status: str, reason: str) -> None:
        meta = self._runs.get(run_id) or {}
        session_id = str(meta.get("session_id") or "")
        if session_id:
            self._broadcast(session_id, {"type": "interrupted", "run_id": run_id,
                                         "reason": reason, "status": status})

    def _spawn_finish(self, run_id: str, session: Session, error: str, interrupted: bool) -> None:
        """在独立任务里做收尾落库。

        为什么不能直接 await：调用点正处于 CancelledError 展开过程中，
        再 await 会被立即二次取消，落库必然丢。这里另起任务并把引用存在
        `_bg_tasks` 上（事件循环只持弱引用，不留引用会被 GC 掉）。
        """
        try:
            task = asyncio.get_running_loop().create_task(self._finish(run_id, session, error, interrupted))
        except RuntimeError:  # 事件循环已关闭：退回同步执行
            self._mark(run_id, status="interrupted", error=error, finished_at=time.time())
            return
        self._bg_tasks.add(task)
        task.add_done_callback(self._bg_tasks.discard)

    async def _finish(self, run_id: str, session: Session, error: str, interrupted: bool) -> None:
        """收尾：会话运行时数据落库 + run 状态归档（在线程池里执行，避免阻塞事件循环）。"""
        status = "interrupted" if interrupted else ("failed" if error else "done")
        # 图在 ask_user 处挂起时事件流是"正常结束"的，但这次运行并没有跑完——
        # 记为 awaiting_input，用户补充信息续跑后才归档为 done。
        if not interrupted and not error and getattr(session, "status", "") == "awaiting_input":
            status = "awaiting_input"
            interrupted = True  # 复用同一分支：不写 finished_at
        meta = self._runs.get(run_id) or {}
        stats = dict(meta.get("stats") or {})
        # 补齐契约里 stats 必填但业务层不产出的字段（否则前端面板显示 0）
        stats["nodes"] = int(meta.get("_nodes") or 0)
        stats["tool_calls"] = max(int(stats.get("tool_calls") or 0), int(meta.get("_tools") or 0))
        started = float(meta.get("started_at") or time.time())
        stats["elapsed_s"] = round(time.time() - started, 1)
        try:
            if self.sessions is not None:
                await asyncio.to_thread(self.sessions.save_runtime, session, run_id)
                await asyncio.to_thread(self.sessions.update_run, run_id, status=status,
                                        error=error, stats=stats)
        except Exception as e:
            print(f"[run_service] 收尾落库失败: {type(e).__name__}: {e}")
        self._mark(run_id, status=status, error=error, stats=stats)
        if not interrupted:
            self._mark(run_id, finished_at=time.time())

    def _mark(self, run_id: str, **fields: Any) -> None:
        with self._lock:
            meta = self._runs.get(run_id)
            if meta is None:
                return
            if "stats" in fields and fields["stats"]:
                merged = dict(meta.get("stats") or {})
                merged.update(fields["stats"])
                fields["stats"] = merged
            meta.update(fields)


__all__ = ["RunService", "RunServiceError", "DEFAULT_CONCURRENCY"]
