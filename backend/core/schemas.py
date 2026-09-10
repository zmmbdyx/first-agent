"""LLM 结构化输出的 Pydantic 校验模型。

为什么需要这一层：
    LLM 返回的 JSON 是**不可信输入**。把它的字段直接喂给执行器会出两类事故：
    1. 结构错位 —— 模型返回 `{"action": "write_report"}`（字符串而非对象），
       执行器 `.get()` 抛 AttributeError，异常穿透整个会话（而不只是这一步失败）；
    2. 语义非法 —— 模型编造不存在的工具名、重复任务 id、依赖不存在的任务、
       或构造出 t1↔t2 互相依赖的死环，导致任务永远无法进入可执行队列。

    Pydantic 在**边界处**一次性挡住这两类问题：类型/长度/取值范围由字段声明约束，
    跨字段的业务规则用 model_validator 表达。校验失败走既定降级路径
    （规划失败 → 确定性模板计划；执行决策失败 → 视为 step 失败），不让脏数据流入。

设计取舍：
    - `Task`（core/memory.py）保持 dataclass 不变——它是**内部状态**且要持久化，
      与"边界校验"职责不同，不强行统一，避免为了用框架而重构数据模型；
    - 本模块只负责校验，不持有状态，可被 planner / agent 共用。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

# 任务状态机（执行器写入，供前端展示）
TASK_STATUSES = ("pending", "running", "done", "failed", "waiting", "skipped")


class PlanTaskSchema(BaseModel):
    """规划器输出的单个子任务。"""

    model_config = ConfigDict(extra="ignore")

    id: str = Field(default="", max_length=64, description="任务 id，计划内唯一")
    title: str = Field(..., min_length=1, max_length=60, description="任务标题")
    detail: str = Field(default="", max_length=300, description="任务说明")
    tool: str = Field(default="none", max_length=64, description="工具名或 none")
    args: Dict[str, Any] = Field(default_factory=dict, description="工具参数")
    depends_on: List[str] = Field(default_factory=list, description="前置任务 id")
    condition: Dict[str, Any] = Field(default_factory=dict, description="条件执行表达式")

    @field_validator("id", "tool", mode="before")
    @classmethod
    def _coerce_str(cls, v):
        """模型偶尔把 id 输出成数字（1 / 2 / 3），统一成字符串。"""
        return "" if v is None else str(v)

    @field_validator("title", mode="before")
    @classmethod
    def _clean_title(cls, v):
        return "" if v is None else str(v).strip()

    @field_validator("depends_on", mode="before")
    @classmethod
    def _clean_deps(cls, v):
        """过滤空值并去重，保持顺序（依赖顺序影响拓扑求值的可读性）。"""
        if not v:
            return []
        if isinstance(v, str):  # 模型可能只给一个字符串
            v = [v]
        out, seen = [], set()
        for item in v:
            if item is None or item == "":
                continue
            s = str(item)
            if s not in seen:
                seen.add(s)
                out.append(s)
        return out

    @field_validator("args", "condition", mode="before")
    @classmethod
    def _clean_dicts(cls, v):
        return v if isinstance(v, dict) else {}


class PlanSchema(BaseModel):
    """规划器整体输出。"""

    model_config = ConfigDict(extra="ignore")

    goal: str = Field(default="", max_length=200)
    tasks: List[PlanTaskSchema] = Field(..., min_length=1, description="子任务列表")

    @field_validator("goal", mode="before")
    @classmethod
    def _clean_goal(cls, v):
        return "" if v is None else str(v).strip()

    @model_validator(mode="after")
    def _check_plan(self):
        """跨字段业务规则：id 唯一、依赖存在、无环、工具名合法。"""
        # ① id 唯一（缺省时按位置补 t1..tn）
        ids: List[str] = []
        for i, t in enumerate(self.tasks):
            if not t.id:
                t.id = f"t{i + 1}"
            ids.append(t.id)
        dupes = {i for i in ids if ids.count(i) > 1}
        if dupes:
            raise ValueError(f"任务 id 重复: {sorted(dupes)}")

        idset = set(ids)
        self_loop = [t.id for t in self.tasks if t.id in t.depends_on]
        if self_loop:
            raise ValueError(f"任务依赖自身: {self_loop}")

        # ② 依赖必须存在
        for t in self.tasks:
            missing = [d for d in t.depends_on if d not in idset]
            if missing:
                raise ValueError(f"任务 {t.id} 依赖不存在的任务: {missing}")

        # ③ 无环：反复剥离"依赖已满足"的节点，剥不完说明有环
        resolved: set[str] = set()
        progress = True
        while progress:
            progress = False
            for t in self.tasks:
                if t.id not in resolved and all(d in resolved for d in t.depends_on):
                    resolved.add(t.id)
                    progress = True
        if len(resolved) != len(self.tasks):
            stuck = [t.id for t in self.tasks if t.id not in resolved]
            raise ValueError(f"任务依赖存在环（永远无法执行）: {stuck}")
        return self

    def known_tool_violations(self, known_tools: set[str]) -> List[str]:
        """返回使用了未知工具的任务 id（none/fail/ask_user 属执行器保留字，不算违规）。"""
        reserved = {"none", "fail", "ask_user", "write_report"}
        bad = []
        for t in self.tasks:
            tool = (t.tool or "none").strip().lower()
            if tool and tool not in known_tools and tool not in reserved:
                bad.append(f"{t.id}:{tool}")
        return bad


class ReactActionSchema(BaseModel):
    """ReAct 单步决策里的 action 对象。"""

    model_config = ConfigDict(extra="ignore")

    tool: str = Field(default="none", max_length=64)
    args: Dict[str, Any] = Field(default_factory=dict)
    reason: str = Field(default="", max_length=300)
    question: str = Field(default="", max_length=500)

    @field_validator("tool", mode="before")
    @classmethod
    def _clean_tool(cls, v):
        return "none" if v is None else str(v).strip().lower()

    @field_validator("args", mode="before")
    @classmethod
    def _clean_args(cls, v):
        return v if isinstance(v, dict) else {}


class ReactDecisionSchema(BaseModel):
    """ReAct 单步决策。

    兼容两种模型输出形态：
      A. 规范：{"thought": "...", "action": {"tool": "...", "args": {...}}, "final": "..."}
      B. 简写：{"thought": "...", "action": "write_report", "args": {...}}   ← 模型常见偏差
    旧实现遇到 B 会抛 AttributeError 并击穿会话，这里统一归一到 A 的结构。
    """

    model_config = ConfigDict(extra="ignore")

    thought: str = Field(default="", max_length=300)
    action: ReactActionSchema = Field(default_factory=ReactActionSchema)
    final: str = Field(default="", max_length=20000)

    @field_validator("thought", "final", mode="before")
    @classmethod
    def _clean_text(cls, v):
        return "" if v is None else str(v)

    @field_validator("action", mode="before")
    @classmethod
    def _normalize_action(cls, v):
        """把字符串 / None / 异常结构统一成 action 对象。"""
        if v is None:
            return {}
        if isinstance(v, str):
            return {"tool": v}
        if isinstance(v, dict):
            # 形如 {"args": "path=x"} 的坏参数：丢弃，避免污染工具调用
            if not isinstance(v.get("args"), (dict, type(None))):
                v = {**v, "args": {}}
            return v
        return {}


def validate_plan_dict(data: Any, known_tools: set[str] | None = None,
                       max_tasks: int = 8) -> Optional[PlanSchema]:
    """校验规划输出；非法返回 None（调用方据此回退到模板计划）。

    未知工具名**不直接判非法**，而是剔除该工具改用 none：
    模型偶发的工具名拼写偏差不值得让整份计划作废，降级为"由模型直接作答"更稳。
    """
    if not isinstance(data, dict):
        return None
    raw_tasks = data.get("tasks")
    if not isinstance(raw_tasks, list) or not raw_tasks:
        return None
    try:
        plan = PlanSchema.model_validate({"goal": data.get("goal"), "tasks": raw_tasks[:max_tasks]})
    except Exception:  # noqa: BLE001 — 校验失败即回退，不向上抛
        return None
    if known_tools:
        bad_ids = {v.split(":", 1)[0] for v in plan.known_tool_violations(known_tools)}
        for t in plan.tasks:
            if t.id in bad_ids:
                t.tool = "none"
    return plan


def validate_react_decision(data: Any) -> ReactDecisionSchema:
    """校验并归一化 ReAct 决策；任何异常输入都退化成"无动作"，绝不抛出。"""
    if not isinstance(data, dict):
        return ReactDecisionSchema()
    try:
        return ReactDecisionSchema.model_validate(data)
    except Exception:  # noqa: BLE001
        return ReactDecisionSchema()
