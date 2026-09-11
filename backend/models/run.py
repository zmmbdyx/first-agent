"""运行表：一次 /api/agent/run 的元信息与终局统计（供 /api/agent/runs/{id} 查询）。"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from models.base import Base, JSONType, gen_id, utcnow


class Run(Base):
    __tablename__ = "runs"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=gen_id)
    session_id: Mapped[str] = mapped_column(String(32), index=True, default="")
    # 归属者与提示词版本：前者用于租户隔离，后者用于质量回归归因（见 HARDENING 第 3 节）
    owner: Mapped[str] = mapped_column(String(64), default="default", index=True)
    prompt_version: Mapped[str] = mapped_column(String(32), default="")
    task: Mapped[str] = mapped_column(Text, default="")
    preset: Mapped[str] = mapped_column(String(32), default="standard")
    workspace: Mapped[str] = mapped_column(String(120), default="default")
    permission_mode: Mapped[str] = mapped_column(String(32), default="workspace_write")
    model: Mapped[str] = mapped_column(String(120), default="")
    # queued / running / done / failed / interrupted
    status: Mapped[str] = mapped_column(String(32), default="queued")
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    finished_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=True)
    stats: Mapped[dict] = mapped_column(JSONType, default=dict)
    error: Mapped[str] = mapped_column(Text, default="")


__all__ = ["Run"]
