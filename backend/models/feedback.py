"""反馈表：用户对某条 Agent 回复的评分与备注（对应评审 P1-4 反馈闭环）。

为什么要独立成表而不是塞进 sessions.facts：
- 评分需要按 run / 时间点聚合（用于质量回归与人工评估），JSON 里做聚合查询代价高；
- 反馈是**用户产出的事实**，与运行态快照（facts 里的 `_runtime`）语义不同，
  混在一起会在回滚会话态时被一起清掉。
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Float, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from models.base import Base, gen_id, utcnow


class Feedback(Base):
    __tablename__ = "feedback"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=gen_id)
    session_id: Mapped[str] = mapped_column(String(32), index=True, default="")
    run_id: Mapped[str] = mapped_column(String(32), index=True, default="")
    owner: Mapped[str] = mapped_column(String(64), default="default", index=True)
    # 1-5 分；0 表示仅备注未评分
    rating: Mapped[int] = mapped_column(Integer, default=0)
    comment: Mapped[str] = mapped_column(Text, default="")
    # 关联到具体消息的时间戳（core.memory.Session.messages[].ts），便于定位是哪条回复
    message_ts: Mapped[float] = mapped_column(Float, default=0.0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


__all__ = ["Feedback"]
