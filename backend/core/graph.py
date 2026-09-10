"""LangGraph 编排层：把既有 `JobAgent` 的流水线组织成状态机（规划 → 调度 → ReAct → 校验 → 综合）。

设计权衡（重要）
----------------
1. **不重写业务规则**：规划(`_make_plan`)、依赖/条件调度、ReAct 步进、工具重试与熔断、
   产物接线、ask_user 恢复、综合报告，全部调用 `core/agent.py` 的既有方法。
   图节点只做四件事：调方法、发事件、记轨迹、决定下一步走哪条边。
2. **state 里不放活对象**：`Session`/`JobAgent`/事件桥接都不可安全序列化，放进检查点会
   造出"半份快照"。这里用 `_RUNTIME` 注册表按 `thread_id` 持有运行时对象，
   state 只保留纯数据（见 `core/state.py`）。
3. **ask_user 的中断放在独立节点**：如果直接在 `react` 里中断，恢复时 LangGraph 会
   从该节点开头重放，导致任务被重复执行（重复工具调用/重复扣费）。因此 `react` 与
   `await_user` 分离：`await_user` 是**无副作用**的纯中断节点，重放安全。
4. **阻塞式业务调用走线程池**：`_run_task` 内含指数退避睡眠与同步 LLM 调用，
   直接在事件循环里跑会卡住 SSE。这里用 `asyncio.to_thread` 执行，
   配合事件桥接的 `loop.call_soon_threadsafe` 把 `ask_user` 立刻叫醒主循环。
5. **接缝缺失也能跑**：`_preflight/_refresh_facts/_next_runnable` 等由并行工作流补充，
   本模块用 `_call_optional` 容错调用——方法不存在时退回既有等价逻辑或安全默认值，
   保证图在做完业务改造前就能端到端跑通。
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, AsyncIterator, Callable

from config import Settings
from core import events as ev
from core.checkpointer import SnapshotStore, build_checkpointer, checkpointer_kind
from core.events import EventBridge
from core.longterm import LongTermMemory
from core.metrics import MetricsCollector
from core.permissions import Permission, resolve_permission
from core.presets import TERSE_SUFFIX, apply_preset, preset_flag, resolve_preset
from core.sandbox import Sandbox, SandboxViolation
from core.state import AgentState, new_agent_state
from services.trajectory import TrajectoryRecorder

logger = logging.getLogger(__name__)

# 固定节点名（契约第 3 节：前端轨迹读条按此显示）
NODE_RECALL = "recall"
NODE_PLAN = "plan"
NODE_DISPATCH = "dispatch"
NODE_REACT = "react"
NODE_CHECK = "check"
NODE_AWAIT_USER = "await_user"
NODE_SYNTHESIZE = "synthesize"
NODE_FINALIZE = "finalize"

NODE_LABELS: dict[str, str] = {
    NODE_RECALL: "记忆召回",
    NODE_PLAN: "任务规划",
    NODE_DISPATCH: "依赖调度",
    NODE_REACT: "工具执行",
    NODE_CHECK: "产物校验",
    NODE_AWAIT_USER: "等待补充",
    NODE_SYNTHESIZE: "综合报告",
    NODE_FINALIZE: "收尾统计",
}

# 基础节点（always-on）；`check` 仅 PTC 预设启用
BASE_NODES = (NODE_RECALL, NODE_PLAN, NODE_DISPATCH, NODE_REACT,
              NODE_SYNTHESIZE, NODE_FINALIZE)

# await_user 的路由护栏：同一轮最多中断次数（用户连续不提供材料时强制收尾）
MAX_ASK_ROUNDS = 6

# 节点内部异常时用于展示的中文标签
_STEP_LABELS = {"recall": "召回", "plan": "规划", "dispatch": "调度", "react": "执行",
                "check": "校验", "resume": "恢复", "finalize": "收尾"}


# ============================================================================
# 容错调用：并行工作流补充的接缝可能尚未就绪
# ============================================================================
def _call_optional(agent: Any, name: str, *args: Any, default: Any = None, **kwargs: Any) -> Any:
    """调用 `agent` 上的可选方法；方法不存在返回 `default`，其余异常照常抛出。

    这样"接缝未就绪"与"业务真的报错"被区分开：前者静默退回既有逻辑，
    后者仍由节点异常处理统一记录到轨迹与 `error` 事件。
    """
    fn = getattr(agent, name, None)
    if not callable(fn):
        return default
    return fn(*args, **kwargs)


def _has(agent: Any, name: str) -> bool:
    return callable(getattr(agent, name, None))


def _bubble_types() -> tuple[type, ...]:
    """LangGraph 的控制流异常类型（中断/跳转）：必须原样上抛，不能被兜底 except 吞掉。"""
    types: list[type] = []
    try:
        from langgraph.errors import GraphBubbleUp
        types.append(GraphBubbleUp)
    except Exception:
        pass
    try:
        import langgraph.errors as _errors
        for attr in dir(_errors):
            obj = getattr(_errors, attr)
            if isinstance(obj, type) and issubclass(obj, BaseException) and obj not in types:
                if attr in ("GraphInterrupt", "ParentCommand", "GraphBubbleUp"):
                    types.append(obj)
    except Exception:
        pass
    return tuple(types) or ()


_BUBBLE_TYPES = _bubble_types()


def emit_bus(agent: Any, etype: str, **data: Any) -> dict | None:
    """安全发射事件：总线异常绝不影响流水线。"""
    bus = getattr(agent, "bus", None)
    if bus is None:
        return None
    try:
        return bus.emit(etype, **data)
    except Exception:
        logger.debug("事件发射失败：%s", etype, exc_info=True)
        return None


# ============================================================================
# 运行时（按 thread_id 注册，检查点里只存纯数据）
# ============================================================================
@dataclass
class RunRuntime:
    """一次 run 的活对象集合。"""

    run_id: str
    thread_id: str
    session_id: str
    agent: Any
    cfg: Settings
    session: Any
    loop: asyncio.AbstractEventLoop
    bridge: EventBridge
    recorder: TrajectoryRecorder
    metrics: MetricsCollector
    permission: Permission
    preset: str
    preset_name: str
    sandbox: Sandbox | None = None
    snapshots: SnapshotStore | None = None
    longterm: Any = None
    workspace: Path | None = None
    check_enabled: bool = False
    max_check_retries: int = 0
    terse_output: bool = False
    check_done: bool = False          # 校验补救只允许一轮
    terminated: bool = False          # 是否已下发 final_answer / run_done（防重复）
    check_retries: int = 0            # 校验节点重试次数（写入 stats）
    memory_hits: int = 0              # 长期记忆召回条数（写入 stats）
    workspace_warning: str = ""
    final_stats: dict = field(default_factory=dict)
    final_content: str = ""
    final_chart: str = ""
    emitted: dict[str, int] = field(default_factory=dict)
    started_at: float = field(default_factory=time.time)
    llm_token_state: dict = field(default_factory=dict)   # 用量回调的增量基线


_RUNTIME: dict[str, RunRuntime] = {}


def _register_runtime(rt: RunRuntime) -> None:
    """注册；同一 thread 重复运行（例如 resume）时替换为新运行时。"""
    previous = _RUNTIME.get(rt.thread_id)
    if previous is not None and previous is not rt:
        try:
            previous.metrics.unbind_llm(previous.agent.llm)
            previous.bridge.unsubscribe()
        except Exception:
            logger.debug("旧运行时清理失败", exc_info=True)
    _RUNTIME[rt.thread_id] = rt


def _drop_runtime(thread_id: str) -> None:
    _RUNTIME.pop(thread_id, None)


def _runtime_for(state: AgentState) -> RunRuntime | None:
    key = str(state.get("thread_id") or state.get("run_id") or "")
    return _RUNTIME.get(key)


# ============================================================================
# 节点包裹：node_start / node_end / 轨迹 / 指标
# ============================================================================
class _Step:
    """把"节点开始/结束"的事件与轨迹记录收敛到一处，避免每个节点重复样板。"""

    def __init__(self, rt: RunRuntime, state: AgentState, node: str, label: str = "",
                 task_id: str = "", **detail: Any):
        self.rt = rt
        self.state = state
        self.node = node
        self.label = label or NODE_LABELS.get(node, node)
        self.task_id = task_id
        self.detail = detail
        self.record = None
        self.t0 = time.monotonic()

    def __enter__(self) -> "_Step":
        self.record = self.rt.recorder.start(self.node, self.label, task_id=self.task_id,
                                             **self.detail)
        emit_bus(self.rt.agent, ev.NODE_START, node_id=self.record.node_id, node=self.node,
                 label=self.label, seq=self.record.seq, task_id=self.task_id or None)
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        self._apply_tool_and_llm_counters()
        elapsed = int((time.monotonic() - self.t0) * 1000)
        status, error = "done", ""
        if exc is not None:
            if _BUBBLE_TYPES and isinstance(exc, _BUBBLE_TYPES):
                # 中断/跳转控制流：按"已挂起"记录，交给上层继续处理
                self.rt.recorder.finish(status="interrupted", elapsed_ms=elapsed)
                emit_bus(self.rt.agent, ev.NODE_END, node_id=self.record.node_id, node=self.node,
                         status="interrupted", elapsed_ms=elapsed,
                         tokens=dict(self.record.tokens), error="")
                self._emit_trajectory()
                return False
            status, error = "failed", f"{exc_type.__name__}: {exc}"
        self.rt.recorder.finish(status=status, elapsed_ms=elapsed, error=error)
        emit_bus(self.rt.agent, ev.NODE_END, node_id=self.record.node_id, node=self.node,
                 status=status, elapsed_ms=elapsed, tokens=dict(self.record.tokens),
                 error=error)
        self._emit_metric()
        self._emit_trajectory()
        return False   # 不吞异常：由节点自身的守卫决定是否降级

    # ---- 内部 ----
    def _apply_tool_and_llm_counters(self) -> None:
        before = self.state.get("counters") or {}
        after = {"llm_calls": int(getattr(self.rt.agent.llm, "calls", 0) or 0),
                 "tool_calls": int(sum(s.get("calls", 0) for s in
                                       getattr(self.rt.agent.registry, "call_stats", {}).values())),
                 "tool_retries": int(sum(s.get("retries", 0) for s in
                                         getattr(self.rt.agent.registry, "call_stats", {}).values()))}
        self.state["counters"] = ev_merge_counters(before, after)   # type: ignore[assignment]

    def _emit_metric(self) -> None:
        snapshot = self.rt.metrics.snapshot()
        self.state["metric"] = snapshot                      # type: ignore[assignment]
        emit_bus(self.rt.agent, ev.METRIC, **snapshot)

    def _emit_trajectory(self) -> None:
        snapshot = self.rt.recorder.snapshot()
        self.state["node_trace"] = snapshot.get("nodes") or []   # type: ignore[assignment]
        emit_bus(self.rt.agent, ev.TRAJECTORY, **snapshot)


def ev_merge_counters(before: Any, after: dict) -> dict:
    """计数器取"运行累计值"：既有 agent 的统计本身是累计的，直接写入即可。"""
    out = dict(before or {})
    out.update(after)
    return out


class _Fail:
    """节点级异常兜底：进入时发 `node_end(failed)`，并让流水线能安全收尾。"""

    def __init__(self, rt: RunRuntime, state: AgentState, node: str):
        self.rt = rt
        self.state = state
        self.node = node

    def __enter__(self) -> "_Fail":
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        if exc is None:
            return False
        if _BUBBLE_TYPES and isinstance(exc, _BUBBLE_TYPES):
            return False    # 中断控制流必须上抛
        label = _STEP_LABELS.get(self.node, self.node)
        message = f"{label}节点执行失败：{type(exc).__name__}: {exc}"
        logger.warning(message)
        self.state["error"] = message
        emit_bus(self.rt.agent, ev.ERROR, message=message)
        try:
            self.rt.recorder.finish(status="failed", error=message)
        except Exception:
            pass
        return True     # 吞掉异常，让图继续收尾（绝不击穿会话）


# ============================================================================
# 图节点
# ============================================================================
def _recall(rt: RunRuntime, state: AgentState) -> dict:
    session = rt.session
    agent = rt.agent
    text = str(state.get("task_text") or "")
    with _Fail(rt, state, NODE_RECALL), _Step(rt, state, NODE_RECALL):
        agent._session = session
        # 每次 run 重置计时起点：既有 `_finalize` 用 `agent._run_started` 计算 elapsed_s，
        # 复用同一 agent 实例时必须重置，否则第二次运行的耗时会显示成 0。
        agent._run_started = time.time()
        handled = False
        if _has(agent, "_preflight"):
            handled = bool(_call_optional(agent, "_preflight", session, text, default=False))
            if not handled:
                emit_bus(agent, ev.USER_MESSAGE, content=text)
        else:
            # 接缝未就绪：只回显用户消息，拦截/分类逻辑仍由既有 handle_message 覆盖
            emit_bus(agent, ev.USER_MESSAGE, content=text)
        if handled:
            return {"handled": True, "done": False}
        # 用户消息入库：与既有 `agent._handle` 完全对齐（图绕过了 `_handle`，
        # 若不补这一步，会话历史/落库服务就看不到用户发言）。
        try:
            session.add_message("user", text)
        except Exception:
            logger.debug("用户消息入库失败", exc_info=True)
        # 刷新画像（含文件路径/岗位等）
        try:
            if _has(agent, "_refresh_facts"):
                _call_optional(agent, "_refresh_facts", session, text)
        except Exception as e:
            emit_bus(agent, ev.ERROR, message=f"画像刷新失败（不影响主流程）: {e}")
        # 长期记忆召回：补齐系统提示之外的跨会话上下文
        hits: list[dict] = []
        if rt.longterm is not None:
            try:
                hits = rt.longterm.search(text, k=3) or []
            except Exception as e:
                logger.debug("长期记忆召回失败：%s", e)
        if hits:
            emit_bus(agent, "memory_recall", query=text[:120],
                     hits=[{"text": h.get("text", "")[:200], "score": h.get("score")} for h in hits])
        return {"handled": False, "memory_hits": hits}


def _plan(rt: RunRuntime, state: AgentState) -> dict:
    agent, session = rt.agent, rt.session
    text = str(state.get("task_text") or "")
    if rt.terse_output:
        text = text + TERSE_SUFFIX
    with _Fail(rt, state, NODE_PLAN), _Step(rt, state, NODE_PLAN):
        goal, tasks = agent._make_plan(session, state.get("task_text") or text)
        session.artifacts = {}     # 与既有 _handle 一致：新目标清空上一轮产物
        session.tasks = list(tasks or [])
        if session.title in ("新会话", "") and goal:
            session.title = str(goal)[:30]
        emit_bus(agent, ev.PLAN_CREATED, goal=goal,
                 tasks=[t.to_dict() for t in session.tasks], costs=agent._tool_costs())
        if not session.tasks:
            emit_bus(agent, ev.ERROR, message="规划未产出任何任务，已直接进入综合阶段")
        session.status = "running"
        return {"plan_goal": str(goal or ""),
                "plan_tasks": [t.to_dict() for t in session.tasks],
                "counters": {"planned_tasks": len(session.tasks)}}


def _propagate(session: Any, agent: Any) -> None:
    """依赖失败传播与条件跳过。

    优先调用既有 `_propagate_dependencies`；接缝未就绪时退化为等价的内联实现——
    注意这套规则**本来就在 `_execute_plan` 里**，这里只是为了在图上让"调度"成为
    可见节点而提前触发一次，语义完全一致。
    """
    if _has(agent, "_propagate_dependencies"):
        _call_optional(agent, "_propagate_dependencies", session)
        return
    for task in session.tasks:
        if task.status != "pending":
            continue
        deps = [session.task(d) for d in task.depends_on]
        if any(d is None or d.status == "failed" for d in deps):
            task.status = "failed"
            task.error = f"前置任务失败: {task.depends_on}"
            emit_bus(agent, ev.TASK_FINISH, task_id=task.id, status="failed", error=task.error)
        elif deps and all(d and d.status in ("done", "skipped") for d in deps):
            met = True
            if hasattr(agent, "_condition_met"):
                try:
                    met = bool(agent._condition_met(session, task.condition))
                except Exception:
                    met = True     # 条件求值失败按执行处理（fail-open）
            if not met:
                task.status = "skipped"
                task.result = "条件不满足，已跳过"
                emit_bus(agent, ev.TASK_FINISH, task_id=task.id, status="skipped",
                         result_brief=task.result)


def _next_runnable(session: Any, agent: Any) -> Any:
    """选下一个就绪任务。优先既有 `_next_runnable`，否则内联等价逻辑。"""
    if _has(agent, "_next_runnable"):
        return _call_optional(agent, "_next_runnable", session)
    runnable = [t for t in session.tasks if t.status == "pending"
                and all((session.task(d) is not None and session.task(d).status in ("done", "skipped"))
                        for d in t.depends_on)]
    return runnable[0] if runnable else None


def _dispatch(rt: RunRuntime, state: AgentState) -> dict:
    session, agent = rt.session, rt.agent
    with _Fail(rt, state, NODE_DISPATCH), _Step(rt, state, NODE_DISPATCH):
        _propagate(session, agent)
        task = _next_runnable(session, agent)
        if task is None:
            # 仍有 pending 但一个都不可运行 → 依赖死锁（正常不该发生）。
            # 连续两次空调度即判败收尾，避免"调度空转"把递归预算烧光。
            streak = int(state.get("empty_dispatch_streak") or 0) + 1
            if _pending_tasks(session) and streak >= 2:
                for pending in _pending_tasks(session):
                    pending.status = "failed"
                    pending.error = pending.error or "依赖无法满足（调度死锁）"
                    emit_bus(agent, ev.TASK_FINISH, task_id=pending.id, status="failed",
                             error=pending.error)
            return {"ready_task_id": "", "current_task_id": "",
                    "empty_dispatch_streak": streak}
        return {"ready_task_id": task.id, "current_task_id": task.id,
                "empty_dispatch_streak": 0, "counters": {"dispatched": 1}}


async def _react(rt: RunRuntime, state: AgentState) -> dict:
    """单任务 ReAct：调用既有 `_run_task`（含重试/熔断/产物接线），并感知 ask_user。"""
    session, agent = rt.session, rt.agent
    task = session.task(state.get("ready_task_id") or state.get("current_task_id"))
    if task is None:
        return {"ready_task_id": "", "current_task_id": ""}
    pending_observation = state.get("observation") if state.get("resume_rounds") else None
    with _Fail(rt, state, NODE_REACT), _Step(rt, state, NODE_REACT, task_id=task.id,
                                             title=task.title, tool=task.tool):
        rt.bridge.clear_ask()
        if pending_observation:
            # 恢复轮次：把用户在追问里的补充作为观察喂回既有执行器（保持单次重试语义）
            try:
                task.steps.append({"thought": "用户补充信息", "tool": "ask_user", "ok": True,
                                   "args": {}, "observation": str(pending_observation)[:800]})
            except Exception:
                logger.debug("写入恢复观察失败", exc_info=True)
        await _run_sync(rt, agent._run_task, session, task, pending_observation)
        awaiting = str(getattr(session, "status", "")) == "awaiting_input"
        done = task.status in ("done", "failed", "skipped")
        return {"ready_task_id": "", "current_task_id": task.id,
                "ask_pending": bool(awaiting),
                "ask_rounds": int(state.get("ask_rounds") or 0) + (1 if awaiting else 0),
                "observation": "",
                "observations": [{"task_id": task.id, "title": task.title, "status": task.status,
                                  "brief": (task.result or task.error or "")[:400],
                                  "tool": task.tool}],
                "done": bool(done and not awaiting)}


async def _await_user(rt: RunRuntime, state: AgentState) -> dict:
    """图中断节点（**无副作用**，可安全重放）：承接既有 `ask_user`。"""
    from langgraph.types import interrupt
    session = rt.session
    record = rt.recorder.start(NODE_AWAIT_USER, NODE_LABELS[NODE_AWAIT_USER])
    emit_bus(rt.agent, ev.NODE_START, node_id=record.node_id, node=NODE_AWAIT_USER,
             label=NODE_LABELS[NODE_AWAIT_USER], seq=record.seq)
    question = str(getattr(session, "pending_question", "") or
                   getattr(session, "_last_question", "") or "请补充必要信息后继续。")
    payload = {"kind": "ask_user", "question": question,
               "task_id": getattr(session, "waiting_task_id", "") or None,
               "run_id": rt.run_id}
    answer = interrupt(payload)     # ← 首次执行在此挂起；恢复时返回用户输入
    emit_bus(rt.agent, ev.NODE_END, node_id=record.node_id, node=NODE_AWAIT_USER,
             status="done", elapsed_ms=0, tokens={}, error="")
    rt.recorder.finish(status="done", elapsed_ms=0)
    return {"ask_answer": str(answer or ""), "ask_pending": False,
            "resume_rounds": int(state.get("resume_rounds") or 0) + 1,
            "observation": str(answer or "")}


async def _resume(rt: RunRuntime, state: AgentState) -> dict:
    """恢复执行：把用户回答交给既有 `_resume`（重新规划 → 继续执行）。"""
    session, agent = rt.session, rt.agent
    answer = str(state.get("ask_answer") or "")
    with _Fail(rt, state, "resume"), _Step(rt, state, "resume", "追问恢复"):
        emit_bus(agent, ev.USER_MESSAGE, content=answer)
        rt.bridge.clear_ask()
        await _run_sync(rt, agent._resume, session, answer)
        awaiting = str(getattr(session, "status", "")) == "awaiting_input"
        return {"ask_pending": bool(awaiting),
                "ask_rounds": int(state.get("ask_rounds") or 0) + (1 if awaiting else 0),
                "plan_tasks": [t.to_dict() for t in session.tasks],
                "done": not awaiting and all(t.status in ("done", "failed", "skipped")
                                             for t in session.tasks)}


def _artifact_issues(artifacts: dict, tasks: list) -> list[str]:
    """产物契约校验（PTC）：只检查"有内容但缺关键字段"与"该产出却没有产物"两类问题。

    刻意**不**检查"内容质量/技能是否齐全"——那是业务判断，不属于编排层职责。
    """
    issues: list[str] = []

    def _num(value: Any) -> float | None:
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    match = artifacts.get("match")
    if isinstance(match, dict) and match:
        score = _num(match.get("score"))
        if score is None:
            issues.append("匹配产物缺少 score 字段")
        elif not (0 <= score <= 100):
            issues.append(f"匹配分数越界：{match.get('score')}")
        if not match.get("missing_skills") and not match.get("matched_skills"):
            issues.append("匹配产物缺少技能比对结果")
    jd = artifacts.get("jd_analysis")
    if isinstance(jd, dict) and jd and not (jd.get("skills_hard") or jd.get("requirements")
                                            or jd.get("responsibilities")):
        issues.append("JD 分析产物缺少技能/职责要点")
    report = artifacts.get("report")
    if isinstance(report, dict) and report and not (report.get("path") or report.get("content")):
        issues.append("报告产物既无落盘路径也无正文")
    search = artifacts.get("search")
    if isinstance(search, dict) and search and not search.get("results"):
        issues.append("检索产物结果为空")
    for task in tasks or []:
        if task.tool == "write_report" and task.status == "done" and not artifacts.get("report"):
            issues.append("write_report 任务已完成但未记录报告产物")
    return issues


def _check(rt: RunRuntime, state: AgentState) -> dict:
    """PTC 校验节点：未通过则把刚完成的任务打回，触发**一次**补救调度。"""
    session = rt.session
    with _Fail(rt, state, NODE_CHECK), _Step(rt, state, NODE_CHECK):
        if str(getattr(session, "status", "")) == "awaiting_input":
            # 任务因缺材料挂起：产物为空是预期状态，不做校验（避免误报 + 误补救）
            return {"check_passed": True, "check_remedy": False}
        issues = _artifact_issues(session.artifacts or {}, session.tasks)
        notes = list(state.get("check_notes") or []) + issues
        retries = int(state.get("check_retries") or 0)
        target = session.task(state.get("current_task_id"))
        remedy = False
        if issues and not rt.check_done and retries < max(1, rt.max_check_retries):
            if target is not None and target.status == "done":
                target.status = "pending"
                target.result = ""
                target.error = ""
                remedy = True
                rt.check_done = True      # 补救只允许一轮，杜绝"校验-补救"死循环
        passed = not issues or not remedy
        emit_bus(rt.agent, "check_result", passed=passed, issues=issues,
                 task_id=target.id if target is not None else None, remedy=remedy)
        return {"check_passed": passed, "check_remedy": remedy,
                "check_retries": retries + (1 if remedy else 0),
                "check_notes": notes,
                "check_last_task": target.id if target is not None else "",
                "counters": {"check_fail": 1 if issues else 0}}


def _prepare_stats(rt: RunRuntime, extra_node: bool = False) -> dict:
    """产出收尾统计并写回运行时。

    统计 = 既有 `agent._finalize` 的 stats（工具/失败任务/磁盘缓存等）
         + 编排层统计（节点数/轨迹节点名/预设/权限）
         + 实时指标（token / tps / 缓存命中率 / 上下文占用）。

    时机很关键：必须在调用既有 `_finalize` **之前**准备好，并把它挂到
    `session._run_stats` 上——既有实现会把同一个 dict 交给 `final_answer` 与
    `run_done`，这样前端拿到的 stats 才包含 `nodes` 等契约字段。
    """
    recorder_stats = rt.recorder.summary()
    names = list(recorder_stats.get("node_names", []))
    if extra_node and NODE_FINALIZE not in names:
        names.append(NODE_FINALIZE)          # 本节点自身尚未记入重复列表
    stats = dict(rt.final_stats or {})
    # 编排层字段**每次重算并覆盖**：`_synthesize` 与 `_finalize` 会各调用一次，
    # 若只 `update` 就会把上一次（少一个节点）的旧值留下来。
    stats.update({
        "nodes": len(names),
        "trajectory_nodes": names,
        "nodes_failed": recorder_stats.get("failed_nodes", 0),
        "node_elapsed_ms": recorder_stats.get("elapsed_ms", 0),
        "trajectory_tool_calls": recorder_stats.get("tool_calls", 0),
        "preset": rt.preset,
        "permission_mode": rt.permission.mode,
        "check_enabled": rt.check_enabled,
        "check_retries": int(getattr(rt, "check_retries", 0) or 0),
        "memory_hits": int(getattr(rt, "memory_hits", 0) or 0),
    })
    metric = rt.metrics.snapshot()
    for key in ("tps", "llm_ms", "prompt_tokens", "completion_tokens", "total_tokens",
                "cached_tokens", "cache_hit_rate", "context_tokens", "context_window",
                "context_ratio", "llm_calls"):
        stats.setdefault(key, metric.get(key))
    stats["tool_calls"] = max(int(stats.get("tool_calls") or 0), int(metric.get("tool_calls") or 0))
    # 耗时以**编排层自己的起点**为准：既有 `_finalize` 用 `agent._run_started` 计算，
    # 一旦 agent 实例被复用或统计被序列化过，就可能残留上一轮的值（例如 0.0 或旧值）。
    stats["elapsed_s"] = round(time.time() - rt.started_at, 1)
    rt.final_stats = stats
    return stats


def _synthesize(rt: RunRuntime, state: AgentState) -> dict:
    session, agent = rt.session, rt.agent
    rt.check_retries = int(state.get("check_retries") or 0)
    rt.memory_hits = len(state.get("memory_hits") or [])
    with _Fail(rt, state, NODE_SYNTHESIZE), _Step(rt, state, NODE_SYNTHESIZE):
        # 挂起等待用户时绝不能收尾：那样会生成一份"材料缺失"的假报告并唤醒前端。
        if str(getattr(session, "status", "")) == "awaiting_input":
            return {"done": False, "ask_pending": True}
        _prepare_stats(rt)               # 先备好含 nodes/metric 的 stats，再让既有逻辑发事件
        try:
            setattr(session, "_run_stats", rt.final_stats)
        except Exception:
            logger.debug("stats 注入会话失败", exc_info=True)
        if str(getattr(session, "status", "")) != "done":
            agent._finalize(session)     # 既有方法：生成报告 + 发 final_answer / run_done
        answer = rt.bridge.last(ev.FINAL_ANSWER) or {}
        if answer:
            rt.final_content = str(answer.get("content") or "")
            rt.final_chart = str(answer.get("chart") or "")
            rt.final_stats = dict(answer.get("stats") or rt.final_stats or {})
            rt.terminated = True     # 既有 `_finalize` 已经发过 final_answer / run_done
        return {"done": True, "final_content": rt.final_content, "final_chart": rt.final_chart,
                "stats": dict(rt.final_stats)}


def _finalize(rt: RunRuntime, state: AgentState) -> dict:
    """收尾统计：合并既有 stats 与编排层统计，并下发最终 metric / trajectory。"""
    with _Step(rt, state, NODE_FINALIZE):
        # 端点：统计里补上本节点自身
        stats = _prepare_stats(rt, extra_node=True)
        metric = rt.metrics.snapshot()
        emit_bus(rt.agent, ev.METRIC, **metric)
        rt.final_content = rt.final_content or str(state.get("final_content") or "")
        if str(getattr(rt.session, "status", "")) != "awaiting_input":
            rt.terminated = True
            # 终态事件**以本节点为准再发一次**（超集口径）：
            # 既有 `agent._finalize` 在 synthesize 节点发过 final_answer / run_done，
            # 但它不知道编排层统计（nodes / trajectory / token / tps）。
            # 这里补发携带完整契约字段的同名事件，前端归约"后到覆盖先到"，
            # 既不改业务代码，也保证 stats 满足契约。
            if rt.final_content:
                emit_bus(rt.agent, ev.FINAL_ANSWER, content=rt.final_content,
                         chart=rt.final_chart, stats=stats)
            emit_bus(rt.agent, ev.RUN_DONE, stats=stats)
        return {"stats": stats, "metric": metric, "done": True}


# ============================================================================
# 路由
# ============================================================================
def _pending_tasks(session: Any) -> list:
    return [t for t in getattr(session, "tasks", []) if t.status == "pending"]


def _route_after_recall(state: AgentState) -> str:
    return NODE_FINALIZE if state.get("handled") else NODE_PLAN


def _route_after_plan(rt: RunRuntime, state: AgentState) -> str:
    """计划就绪 → 调度；需要用户补充材料（首个任务是 ask_user）→ 直接进中断节点。

    注意：`ask_user` 任务由既有 `_run_task` 置 `session.status=awaiting_input` 并返回，
    这里提前分流只是为了让"缺材料"这一路径少绕一圈，行为与 `_execute_plan` 一致。
    """
    if str(getattr(rt.session, "status", "")) == "awaiting_input":
        return NODE_AWAIT_USER
    return NODE_DISPATCH if state.get("plan_tasks") else NODE_SYNTHESIZE


def _route_await_user(state: AgentState) -> str:
    return NODE_AWAIT_USER if state.get("ask_pending") else NODE_REACT


def _route_after_await(rt: RunRuntime, state: AgentState) -> str:
    if int(state.get("ask_rounds") or 0) >= MAX_ASK_ROUNDS:
        emit_bus(rt.agent, ev.ERROR,
                 message=f"用户连续 {MAX_ASK_ROUNDS} 轮未提供材料，已终止追问并直接收尾")
        return NODE_SYNTHESIZE
    return NODE_DISPATCH if state.get("ask_pending") else NODE_REACT


def _route_after_react(rt: RunRuntime, state: AgentState) -> str:
    session = rt.session
    if state.get("ask_pending") or str(getattr(session, "status", "")) == "awaiting_input":
        return NODE_DISPATCH
    if rt.check_enabled:
        return NODE_CHECK
    return NODE_DISPATCH if _pending_tasks(session) else NODE_SYNTHESIZE


def _route_after_check(rt: RunRuntime, state: AgentState) -> str:
    """校验后：未通过 → 回到 dispatch 触发一次补救；否则**只要还有就绪任务就继续调度**，
    只有全部任务结束才进综合（否则 PTC 会在第一个任务后就把剩余任务全部丢掉）。
    """
    if state.get("check_remedy"):
        return NODE_DISPATCH
    return NODE_DISPATCH if (state.get("ready_task_id") or _pending_tasks(rt.session)) \
        else NODE_SYNTHESIZE


def _route_dispatch(rt: RunRuntime, state: AgentState) -> str:
    session = rt.session
    if str(getattr(session, "status", "")) == "awaiting_input":
        return NODE_AWAIT_USER
    if state.get("ready_task_id"):
        return NODE_REACT
    if _pending_tasks(session) and int(state.get("empty_dispatch_streak") or 0) < 2:
        # 依赖尚未满足但仍有 pending：再调度一轮（上一轮可能刚放行了新任务）。
        # 连续两次选不出任务则视为依赖死锁，交给 `_dispatch` 判败后收尾。
        return NODE_DISPATCH
    return NODE_SYNTHESIZE


def _route_after_resume(rt: RunRuntime, state: AgentState) -> str:
    session = rt.session
    if state.get("ask_pending") or str(getattr(session, "status", "")) == "awaiting_input":
        return NODE_AWAIT_USER
    return NODE_DISPATCH if _pending_tasks(session) else NODE_SYNTHESIZE


# ============================================================================
# 图构建
# ============================================================================
def build_agent_graph(agent: Any, checkpointer: Any = None, *, runtime: RunRuntime | None = None,
                      check_enabled: bool = False) -> Any:
    """构建并编译状态机。

    - `agent`：既有 `JobAgent`（业务规则的唯一来源）；
    - `checkpointer`：检查点保存器，`None` 时由调用方/`astream_run` 决定；
    - `runtime`：一次 run 的活对象；由 `astream_run` 注入（闭包捕获，不进 state）；
    - `check_enabled`：PTC 预设启用 `check` 校验节点。
    """
    from langgraph.graph import END, START, StateGraph

    graph: Any = StateGraph(AgentState)

    def node_recall(state: AgentState) -> dict:
        return _recall(_rt(runtime, state), state)

    def node_plan(state: AgentState) -> dict:
        return _plan(_rt(runtime, state), state)

    def node_dispatch(state: AgentState) -> dict:
        return _dispatch(_rt(runtime, state), state)

    async def node_react(state: AgentState) -> dict:
        return await _react(_rt(runtime, state), state)

    def node_check(state: AgentState) -> dict:
        return _check(_rt(runtime, state), state)

    async def node_await_user(state: AgentState) -> dict:
        return await _await_user(_rt(runtime, state), state)

    async def node_resume(state: AgentState) -> dict:
        return await _resume(_rt(runtime, state), state)

    def node_synthesize(state: AgentState) -> dict:
        return _synthesize(_rt(runtime, state), state)

    def node_finalize(state: AgentState) -> dict:
        return _finalize(_rt(runtime, state), state)

    graph.add_node(NODE_RECALL, node_recall)
    graph.add_node(NODE_PLAN, node_plan)
    graph.add_node(NODE_DISPATCH, node_dispatch)
    graph.add_node(NODE_REACT, node_react)
    graph.add_node(NODE_AWAIT_USER, node_await_user)
    graph.add_node("resume", node_resume)          # await_user 的续跑臂（非契约节点名，内部使用）
    graph.add_node(NODE_SYNTHESIZE, node_synthesize)
    graph.add_node(NODE_FINALIZE, node_finalize)
    use_check = bool(check_enabled or (runtime.check_enabled if runtime else False))
    if use_check:
        graph.add_node(NODE_CHECK, node_check)

    graph.add_edge(START, NODE_RECALL)
    graph.add_conditional_edges(NODE_RECALL, _route_after_recall,
                               {NODE_PLAN: NODE_PLAN, NODE_FINALIZE: NODE_FINALIZE})
    graph.add_conditional_edges(
        NODE_PLAN, lambda s: _route_after_plan(_rt(runtime, s), s),
        {NODE_DISPATCH: NODE_DISPATCH, NODE_SYNTHESIZE: NODE_SYNTHESIZE,
         NODE_AWAIT_USER: NODE_AWAIT_USER})
    graph.add_conditional_edges(
        NODE_DISPATCH, lambda s: _route_dispatch(_rt(runtime, s), s),
        {NODE_REACT: NODE_REACT, NODE_AWAIT_USER: NODE_AWAIT_USER,
         NODE_SYNTHESIZE: NODE_SYNTHESIZE, NODE_DISPATCH: NODE_DISPATCH})
    graph.add_conditional_edges(
        NODE_REACT, lambda s: _route_after_react(_rt(runtime, s), s),
        {NODE_CHECK: NODE_CHECK, NODE_DISPATCH: NODE_DISPATCH, NODE_SYNTHESIZE: NODE_SYNTHESIZE}
        if use_check else {NODE_DISPATCH: NODE_DISPATCH, NODE_SYNTHESIZE: NODE_SYNTHESIZE})
    graph.add_edge(NODE_AWAIT_USER, "resume")
    graph.add_conditional_edges(
        "resume", lambda s: _route_after_resume(_rt(runtime, s), s),
        {NODE_AWAIT_USER: NODE_AWAIT_USER, NODE_DISPATCH: NODE_DISPATCH,
         NODE_SYNTHESIZE: NODE_SYNTHESIZE})
    if use_check:
        graph.add_conditional_edges(
            NODE_CHECK, lambda s: _route_after_check(_rt(runtime, s), s),
            {NODE_DISPATCH: NODE_DISPATCH, NODE_SYNTHESIZE: NODE_SYNTHESIZE})
    graph.add_edge(NODE_SYNTHESIZE, NODE_FINALIZE)
    graph.add_edge(NODE_FINALIZE, END)
    # await_user 的入口边在 dispatch 上（契约：dispatch → await_user）
    return graph.compile(checkpointer=checkpointer)


def _rt(runtime: RunRuntime | None, state: AgentState) -> RunRuntime:
    """取运行时：优先闭包捕获（正常路径），回退到注册表（跨调用复用同一张图时）。"""
    if runtime is not None:
        return runtime
    found = _runtime_for(state)
    if found is None:
        raise RuntimeError("缺少运行时上下文：请通过 astream_run 驱动图，或显式传入 runtime")
    return found


# ============================================================================
# 运行驱动
# ============================================================================
async def _run_sync(rt: RunRuntime, fn: Callable, *args: Any) -> Any:
    """在线程池里执行阻塞式业务调用，并在 ask_user 发生时被唤醒（不中断业务线程）。

    关键点：这里**不取消**工作线程——`_run_task` 在 ask_user 时已经自行返回，
    取消反而会留下半执行的工具调用。停止等待的那一刻会 `cancel()` 唤醒协程，
    避免留下悬挂任务；业务线程继续跑到自然结束。
    """
    work = asyncio.ensure_future(asyncio.to_thread(fn, *args))
    waiter = asyncio.ensure_future(rt.bridge.wait_ask())
    try:
        await asyncio.wait({work, waiter}, return_when=asyncio.FIRST_COMPLETED)
    finally:
        if not waiter.done():
            waiter.cancel()
        if not work.done():
            try:
                await work      # ask_user 后 `_run_task` 会很快返回，等它干净收尾
            except Exception as e:
                emit_bus(rt.agent, ev.ERROR, message=f"执行线程异常: {type(e).__name__}: {e}")
    if work.done() and not work.cancelled():
        exc = work.exception()
        if exc is not None:
            raise exc
        return work.result()
    return None


class GraphRunner:
    """一次 run 的驱动：装配运行时 → 流式跑图 → 收尾（快照/指标/事件）。"""

    def __init__(self, agent: Any, session: Any, task_text: str, cfg: Settings | None = None,
                 **opts: Any):
        self.agent = agent
        self.session = session
        self.task_text = task_text or ""
        self.cfg: Settings = cfg or getattr(agent, "cfg", None) or _default_cfg()
        self.opts = opts
        self.preset = resolve_preset(opts.get("preset"))
        # 关键：预设必须真正落到配置副本上，否则 check 节点等预设开关永远不会生效
        # （`preset_flag` 读的就是副本上的旁路属性）。`apply_preset` 不改全局配置。
        self.cfg = apply_preset(self.cfg, self.preset.id)
        self.permission = resolve_permission(opts.get("permission_mode"), self.cfg)
        self.run_id = str(opts.get("run_id") or _new_run_id())
        self.thread_id = str(opts.get("thread_id") or f"{getattr(session, 'id', 'session')}:{self.run_id}")
        self.resume = bool(opts.get("resume"))
        self.checkpointer = opts.get("checkpointer")
        self._owns_checkpointer = self.checkpointer is None
        self.snapshots: SnapshotStore | None = opts.get("snapshot_store")
        self.recorder = TrajectoryRecorder(self.run_id, str(getattr(session, "id", "")))
        self.metrics = MetricsCollector(_context_window(self.cfg))
        self.bridge = EventBridge(agent, recorder=self.recorder, metrics=self.metrics)
        self.rt: RunRuntime | None = None
        self.graph: Any = None
        self._events_seen = 0

    # ---------------- 装配 ----------------
    def build(self) -> RunRuntime:
        loop = asyncio.get_running_loop()
        self.bridge.attach_loop(loop)
        if self.checkpointer is None:
            self.checkpointer = build_checkpointer(self.cfg, getattr(self.cfg, "sessions_dir", None))
        if self.snapshots is None:
            self.snapshots = SnapshotStore(self.opts.get("snapshot_dir"))
        sandbox, workspace, warning = _make_sandbox(self.cfg, self.session, self.permission,
                                                    self.opts.get("workspace"))
        rt = RunRuntime(
            run_id=self.run_id, thread_id=self.thread_id,
            session_id=str(getattr(self.session, "id", "")), agent=self.agent, cfg=self.cfg,
            session=self.session, loop=loop, bridge=self.bridge, recorder=self.recorder,
            metrics=self.metrics, permission=self.permission, preset=self.preset.id,
            preset_name=self.preset.name, sandbox=sandbox, snapshots=self.snapshots,
            longterm=self.opts.get("longterm"), workspace=workspace,
            check_enabled=bool(preset_flag(self.cfg, "preset_check", False)),
            max_check_retries=int(preset_flag(self.cfg, "preset_max_check_retries", 0) or 0),
            terse_output=bool(preset_flag(self.cfg, "terse_output", False)),
            workspace_warning=warning or self.permission.warning)
        if rt.longterm is None:
            try:
                rt.longterm = LongTermMemory(self.cfg)
            except Exception as e:      # 长期记忆不可用不影响主链路
                logger.info("长期记忆不可用（%s），跳过召回", e)
                rt.longterm = None
        self.rt = rt
        self.graph = build_agent_graph(self.agent, self.checkpointer, runtime=rt,
                                       check_enabled=rt.check_enabled)
        self._maybe_set_model()
        return rt

    def _maybe_set_model(self) -> None:
        model = str(self.opts.get("model") or "").strip()
        llm = getattr(self.agent, "llm", None)
        if not model or llm is None:
            return
        try:
            llm.set_model(model)
        except Exception as e:
            emit_bus(self.agent, ev.ERROR, message=f"模型切换失败: {e}")

    def subscribe(self) -> None:
        self.bridge.subscribe()
        self.metrics.bind_llm(getattr(self.agent, "llm", None))
        self.metrics.add_listener(self._on_usage)

    def _on_usage(self, snapshot: dict) -> None:
        """每次 LLM 用量回调 → 归因到当前节点 + 下发 `metric` 事件（契约要求实时）。"""
        rt = self.rt
        if rt is None:
            return
        base = rt.llm_token_state
        delta = {}
        for key in ("prompt_tokens", "completion_tokens", "total_tokens", "cached_tokens"):
            delta[key] = max(0, int(snapshot.get(key, 0)) - int(base.get(key, 0)))
            base[key] = int(snapshot.get(key, 0))
        rt.recorder.add_tokens(**delta)
        emit_bus(self.agent, ev.METRIC, **snapshot)

    # ---------------- 初始状态 ----------------
    def initial_state(self) -> AgentState:
        assert self.rt is not None
        state = new_agent_state(
            run_id=self.run_id, session_id=self.rt.session_id, thread_id=self.thread_id,
            task_text=self.task_text, preset=self.preset.id,
            permission_mode=self.permission.mode, permission=self.permission.to_dict(),
            workspace=str(self.rt.workspace or ""), model=str(self.opts.get("model") or
                                                             getattr(getattr(self.agent, "llm", None),
                                                                     "model", "") or ""),
            reasoning_effort=str(self.opts.get("reasoning_effort") or "medium"))
        state["pending_question"] = str(getattr(self.session, "pending_question", "") or "")
        return state

    def config(self, state: AgentState | None = None) -> dict:
        tasks = len((state or {}).get("plan_tasks") or []) or 4
        return {"configurable": {"thread_id": self.thread_id, "checkpoint_ns": ""},
                "recursion_limit": max(25, 8 * tasks + 14)}

    # ---------------- 流式执行 ----------------
    async def run(self) -> AsyncIterator[dict]:
        rt = self.rt
        assert rt is not None
        yield {"type": ev.RUN_STARTED, "run_id": self.run_id, "session_id": rt.session_id,
               "model": str(getattr(getattr(self.agent, "llm", None), "model", "")),
               "preset": self.preset.id, "permission_mode": self.permission.mode,
               "workspace": str(rt.workspace or "")}
        yield {"type": ev.SESSION_INFO, "session_id": rt.session_id,
               "status": str(getattr(self.session, "status", "")),
               "title": str(getattr(self.session, "title", ""))}
        if rt.workspace_warning:
            yield {"type": ev.ERROR, "message": rt.workspace_warning}

        payload: Any
        state = self.initial_state()
        if self.resume:
            from langgraph.types import Command
            payload = Command(resume=self.task_text)
            emit_bus(self.agent, "resumed", answer=self.task_text[:200])
        else:
            payload = state

        async for chunk in self.graph.astream(payload, config=self.config(state),
                                              stream_mode="updates"):
            for event in self._drain():
                yield event
            nodes = self._node_names(chunk)
            for node in nodes:
                yield self._trajectory_event(node)
            # `finalize` 是终点节点：它跑完后还要再拉一次事件队列，
            # 才能把收尾节点里发出的最终 metric / run_done 交给消费端。
            # （不能在 `synthesize` 后就提前跳出，否则 finalize 的收尾统计会丢。）
            if NODE_FINALIZE in nodes:
                for event in self._final_drain():
                    yield event
                break
        for event in self._final_drain():
            yield event

    def _node_names(self, chunk: Any) -> list[str]:
        if not isinstance(chunk, dict):
            return []
        return [str(k) for k in chunk.keys() if k in NODE_LABELS or k == "resume"]

    def _drain(self) -> list[dict]:
        return self.bridge.drain()

    def _final_drain(self) -> list[dict]:
        return self.bridge.drain()

    def _trajectory_event(self, node: str) -> dict:
        """节点结束后增量推送整份轨迹快照（契约：整份推送，前端按 node_id 覆盖）。"""
        snapshot = self.recorder.snapshot()
        snapshot.setdefault("node", node)
        return {"type": ev.TRAJECTORY, **snapshot}

    # ---------------- 收尾 ----------------
    async def teardown(self, error: Exception | None = None) -> AsyncIterator[dict]:
        rt = self.rt
        if rt is None:
            return
        # 成交前的最后一次搬运：可能还有工作线程刚写入的事件
        for event in self.bridge.drain():
            yield event
        checkpoint_id = self._checkpoint_id()
        if checkpoint_id:
            try:
                self.rt.snapshots.save(rt.session_id, checkpoint_id, self.session,
                                       meta={"run_id": self.run_id, "node": "run_end",
                                             "preset": rt.preset})
            except Exception as e:
                logger.info("运行快照保存失败：%s", e)
        stats = dict(rt.final_stats or {})
        stats.setdefault("elapsed_s", round(time.time() - rt.started_at, 1))
        if not stats.get("trajectory_nodes"):
            stats["trajectory_nodes"] = self.recorder.summary().get("node_names", [])
        if error is not None:
            message = f"编排异常：{type(error).__name__}: {error}"
            emit_bus(self.agent, ev.ERROR, message=message)
        awaiting = str(getattr(self.session, "status", "")) == "awaiting_input"
        emitted_done = bool(self.bridge.history((ev.RUN_DONE,)))
        if emitted_done or awaiting:
            # 终态事件已经发过（或本轮是挂起收尾）：只补中断事件，不重复终态事件。
            if awaiting and not error:
                yield {"type": ev.INTERRUPTED, "run_id": self.run_id, "reason": "ask_user",
                       "question": str(getattr(self.session, "pending_question", "") or "")}
        else:
            answer = self.bridge.last(ev.FINAL_ANSWER) or {}
            content = rt.final_content or str(answer.get("content") or "")
            if content:
                yield {"type": ev.FINAL_ANSWER, "content": content,
                       "chart": rt.final_chart or str(answer.get("chart") or ""), "stats": stats}
            yield {"type": ev.RUN_DONE, "stats": stats}
        # 轨迹与指标的最后一份快照（保证前端读到完整节点清单）
        yield {"type": ev.TRAJECTORY, **self.recorder.snapshot()}
        yield {"type": ev.METRIC, **self.metrics.snapshot()}
        # 资源回收
        try:
            self.bridge.unsubscribe()
        finally:
            self.metrics.unbind_llm(getattr(self.agent, "llm", None))
            _drop_runtime(self.thread_id)

    def _pending_interrupt(self) -> bool:
        try:
            snapshot = self.graph.get_state(self.config())
            return bool(getattr(snapshot, "next", ()))
        except Exception:
            return False

    def _checkpoint_id(self) -> str:
        """取最近检查点 id（回滚锚点）；框架保存器与自带实现都支持。"""
        try:
            snapshot = self.graph.get_state(self.config())
            config = getattr(snapshot, "config", None) or {}
            cid = (config.get("configurable") or {}).get("checkpoint_id")
            if cid:
                return str(cid)
        except Exception:
            logger.debug("读取检查点 id 失败", exc_info=True)
        try:
            cfg = self.checkpointer.get_tuple(self.config())
            if cfg is not None:
                return str(((cfg.config or {}).get("configurable") or {}).get("checkpoint_id") or "")
        except Exception:
            logger.debug("保存器直读检查点 id 失败", exc_info=True)
        return ""

    def checkpointer_kind(self) -> str:
        return checkpointer_kind(self.checkpointer)


async def astream_run(agent: Any, session: Any, task_text: str,
                      cfg: Settings | None = None, **opts: Any) -> AsyncIterator[dict]:
    """驱动一次编排，产出契约（2.2）定义的 SSE 事件 dict。

    关键约束：**绝不把生成器包在 try/except 里**——生成器内部的异常无法在外部捕获，
    会把错误静默变成 `StopAsyncIteration`。这里用一个显式的异常队列把错误带到消费点。
    """
    runner = GraphRunner(agent, session, task_text, cfg, **opts)
    errors: list[BaseException] = []

    async def source() -> AsyncIterator[dict]:
        try:
            rt = runner.build()
            runner.subscribe()
            emit_bus(agent, ev.QUEUE_POSITION, run_id=rt.run_id, position=0,
                     priority=opts.get("priority", 5))
            async for event in runner.run():
                yield event
            async for event in runner.teardown():
                yield event
        except BaseException as e:      # noqa: BLE001 - 统一带回消费点
            errors.append(e)

    try:
        async for event in source():    # 消费时不在 try 内 yield，避免异常被"生成器化"
            yield event
    except BaseException as e:          # 消费端自身异常（如客户端断开）
        errors.append(e)

    if errors:
        error = errors[0]
        if _BUBBLE_TYPES and isinstance(error, _BUBBLE_TYPES):
            raise error                # 中断控制流按原样传递（由服务层决定 resume）
        rt = runner.rt
        message = f"{type(error).__name__}: {error}"
        logger.exception("编排失败：%s", message)
        if rt is not None:
            try:
                for event in rt.bridge.drain():
                    yield event
            except Exception:
                pass
            yield {"type": ev.ERROR, "message": message}
            yield {"type": ev.TRAJECTORY, **rt.recorder.snapshot()}
            try:
                rt.bridge.unsubscribe()
            except Exception:
                pass
            _drop_runtime(rt.thread_id)
        else:
            yield {"type": ev.ERROR, "message": message}


def _make_sandbox(cfg: Settings, session: Any, permission: Permission,
                  workspace: Any) -> tuple[Sandbox | None, Path | None, str]:
    """准备沙箱与工作区目录（失败只告警，不阻断 run）。"""
    warning = ""
    root = Path(str(workspace)) if workspace else Path(getattr(cfg, "workspace_path", Path.cwd()))
    if not root.is_absolute():
        root = Path(getattr(cfg, "workspace_path", Path.cwd())) / root
    try:
        root.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        warning = f"工作区目录不可写（{root}）：{e}，已退回项目工作区根"
        root = Path(getattr(cfg, "workspace_path", Path.cwd()))
    try:
        sandbox = Sandbox(cfg, permission=permission, workspace_root=root)
    except Exception as e:
        logger.info("沙箱初始化失败（%s），本轮不提供沙箱能力", e)
        return None, root, warning
    return sandbox, root, warning


def _context_window(cfg: Settings) -> int:
    try:
        value = int(getattr(cfg, "context_window", 0) or 0)
    except (TypeError, ValueError):
        value = 0
    return value


def _new_run_id() -> str:
    import uuid
    return "r" + uuid.uuid4().hex[:12]


def _default_cfg() -> Settings:
    from config import load_config
    return load_config()


__all__ = ["build_agent_graph", "astream_run", "RunRuntime", "GraphRunner", "NODE_LABELS",
           "NODE_RECALL", "NODE_PLAN", "NODE_DISPATCH", "NODE_REACT", "NODE_CHECK",
           "NODE_AWAIT_USER", "NODE_SYNTHESIZE", "NODE_FINALIZE",
           "apply_preset", "resolve_permission", "SandboxViolation"]
