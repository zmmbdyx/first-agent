"""任务表：计划中的子任务及其执行结果（对应 core.memory.Task 的持久化形态）。"""
from __future__ import annotations

from sqlalchemy import Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from models.base import Base, JSONType, gen_id


class Task(Base):
    __tablename__ = "tasks"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=gen_id)
    run_id: Mapped[str] = mapped_column(String(32), index=True, default="")
    session_id: Mapped[str] = mapped_column(String(32), index=True, default="")
    seq: Mapped[int] = mapped_column(Integer, default=0)
    title: Mapped[str] = mapped_column(String(300), default="")
    detail: Mapped[str] = mapped_column(Text, default="")
    tool: Mapped[str] = mapped_column(String(64), default="none")
    # pending/running/done/failed/waiting/skipped
    status: Mapped[str] = mapped_column(String(32), default="pending")
    result: Mapped[str] = mapped_column(Text, default="")
    error: Mapped[str] = mapped_column(Text, default="")
    retries: Mapped[int] = mapped_column(Integer, default=0)
    elapsed_ms: Mapped[int] = mapped_column(Integer, default=0)
    # 契约中 tasks 只有 tokens 一个 JSON 列，故规划期信息（args/depends_on/condition）
    # 与 ReAct 步骤明细（steps）统一收敛到 tokens 的保留键下，避免新增契约外的列
    tokens: Mapped[dict] = mapped_column(JSONType, default=dict)


__all__ = ["Task"]
