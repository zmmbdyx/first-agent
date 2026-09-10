"""`AgentState`（TypedDict）与 reducer。

设计权衡
--------
1. **state 里不放"活对象"**：`Session` / `JobAgent` / 事件桥接都不是可安全序列化的状态
   （它们在一次 run 内被原地修改），放进去会让检查点变成"半份快照"并在恢复时产生
   两个互不同步的 Session。因此 state 只保留**纯数据**（dict/list/str/int/bool），
   真正的对象由 `graph.py` 的运行时注册表按 `thread_id` 持有。
2. 轨迹与计数用 reducer 表达**合并语义**：节点并行/重放时，轨迹按 seq 归并去重、
   计数相加；其它字段一律"后写覆盖"（单写者，语义简单可预测）。
3. reducer 保持纯函数、无副作用：LangGraph 可能在任意超步调用它们。
"""
from __future__ import annotations

from typing import Annotated, Any, TypedDict


def _as_list(value: Any) -> list:
    return list(value) if isinstance(value, (list, tuple)) else []


def merge_trace(left: Any, right: Any) -> list[dict]:
    """轨迹合并：按 `node_id` 去重，`right` 覆盖同 id 的旧值，最后按 `seq` 升序。

    这样"同一节点被重放/补救后重跑"不会在轨迹里出现两条重复读条，
    也不会丢历史（旧节点仍在，只是同 id 被刷新）。
    """
    items = _as_list(left) + _as_list(right)
    merged: dict[str, dict] = {}
    order: list[str] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        key = str(item.get("node_id") or f"anon-{len(order)}")
        if key not in merged:
            order.append(key)
        merged[key] = {**merged.get(key, {}), **item}
    return sorted((merged[k] for k in order), key=lambda d: int(d.get("seq") or 0))


def merge_observations(left: Any, right: Any) -> list[dict]:
    """观察结果（工具/任务产出摘要）追加合并，限制总量避免 state 膨胀。"""
    items = _as_list(left) + _as_list(right)
    return items[-100:]


def add_counters(left: Any, right: Any) -> dict:
    """计数器相加（llm_calls / tool_calls 等按超步累加）。"""
    out: dict[str, int] = {}
    for src in (left, right):
        if not isinstance(src, dict):
            continue
        for key, value in src.items():
            try:
                out[str(key)] = out.get(str(key), 0) + int(value or 0)
            except (TypeError, ValueError):
                continue
    return out


def take_last(left: Any, right: Any) -> Any:
    """显式"后写覆盖"：用于需要表达 LangGraph 更新语义的字段（可读性优于默认行为）。"""
    return right if right is not None else left


class AgentState(TypedDict, total=False):
    """LangGraph 编排状态（全部可选：新 run 只填必要字段，恢复时由检查点补齐）。"""

    # ---- 标识 ----
    run_id: str
    session_id: str
    thread_id: str
    task_text: str              # 本轮用户原始输入（恢复时用作 resume 载荷）
    preset: str
    permission_mode: str
    permission: dict            # Permission.to_dict() 的快照（纯数据）
    workspace: str
    model: str
    reasoning_effort: str

    # ---- 流水线控制 ----
    handled: bool               # recall 阶段已处理完毕（敏感拦截/闲聊/情绪）→ 直接收尾
    plan_goal: str
    plan_tasks: list[dict]      # 计划任务的纯数据快照（供检查点/前端展示）
    ready_task_id: str          # dispatch 选中的就绪任务 id
    current_task_id: str
    ask_pending: bool           # 某任务发起了 ask_user，需图中断
    ask_answer: str             # 用户回答（unpause 后写入）
    ask_rounds: int
    resume_rounds: int
    observation: str            # 恢复轮次要喂回执行器的用户补充
    empty_dispatch_streak: int  # 连续空调度次数（依赖死锁护栏）
    check_passed: bool
    check_retries: int
    check_notes: list[str]
    check_remedy: bool          # 校验未通过 → 触发一次补救
    check_last_task: str
    done: bool

    # ---- 纯数据产出 ----
    memory_hits: list[dict]     # 长期记忆召回结果
    observations: Annotated[list[dict], merge_observations]
    artifacts_brief: dict
    final_content: str
    final_chart: str
    stats: dict
    metric: dict
    node_trace: Annotated[list[dict], merge_trace]
    counters: Annotated[dict, add_counters]
    snapshot_id: str            # 回滚锚点（注意：不能叫 checkpoint_id，框架保留该通道名）
    error: str


def new_agent_state(**overrides: Any) -> AgentState:
    """构造一份带默认值的初始状态。"""
    state: AgentState = {
        "handled": False,
        "plan_goal": "",
        "plan_tasks": [],
        "ready_task_id": "",
        "current_task_id": "",
        "ask_pending": False,
        "ask_answer": "",
        "ask_rounds": 0,
        "resume_rounds": 0,
        "observation": "",
        "empty_dispatch_streak": 0,
        "check_passed": True,
        "check_retries": 0,
        "check_notes": [],
        "check_remedy": False,
        "check_last_task": "",
        "done": False,
        "memory_hits": [],
        "observations": [],
        "artifacts_brief": {},
        "final_content": "",
        "final_chart": "",
        "stats": {},
        "metric": {},
        "node_trace": [],
        "counters": {},
        "snapshot_id": "",
        "error": "",
    }
    state.update({k: v for k, v in overrides.items() if v is not None})  # type: ignore[typeddict-item]
    return state


__all__ = ["AgentState", "new_agent_state", "merge_trace", "merge_observations",
           "add_counters", "take_last"]
