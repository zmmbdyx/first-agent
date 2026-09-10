"""工具调用表：每次工具调用的入参/简报/耗时/缓存命中情况，供右侧面板与轨迹详情使用。"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from models.base import Base, JSONType, gen_id, utcnow


class ToolCall(Base):
    __tablename__ = "tool_calls"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=gen_id)
    run_id: Mapped[str] = mapped_column(String(32), index=True, default="")
    session_id: Mapped[str] = mapped_column(String(32), index=True, default="")
    task_id: Mapped[str] = mapped_column(String(32), default="")
    tool: Mapped[str] = mapped_column(String(64), default="")
    args: Mapped[dict] = mapped_column(JSONType, default=dict)
    brief: Mapped[str] = mapped_column(Text, default="")
    ok: Mapped[bool] = mapped_column(Boolean, default=True)
    error: Mapped[str] = mapped_column(Text, default="")
    elapsed_ms: Mapped[int] = mapped_column(Integer, default=0)
    cached: Mapped[bool] = mapped_column(Boolean, default=False)
    tokens: Mapped[dict] = mapped_column(JSONType, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


__all__ = ["ToolCall"]
