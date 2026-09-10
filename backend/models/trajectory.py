"""轨迹节点表：LangGraph 节点级轨迹快照，前端「执行轨迹读条」的数据源。"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from models.base import Base, JSONType, gen_id, utcnow


class TrajectoryNode(Base):
    __tablename__ = "trajectory_nodes"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=gen_id)
    run_id: Mapped[str] = mapped_column(String(32), index=True, default="")
    session_id: Mapped[str] = mapped_column(String(32), index=True, default="")
    seq: Mapped[int] = mapped_column(Integer, default=0)
    node: Mapped[str] = mapped_column(String(64), default="")
    label: Mapped[str] = mapped_column(String(200), default="")
    # running / done / failed / skipped / interrupted
    status: Mapped[str] = mapped_column(String(32), default="running")
    elapsed_ms: Mapped[int] = mapped_column(Integer, default=0)
    input: Mapped[dict] = mapped_column(JSONType, default=dict)
    output: Mapped[dict] = mapped_column(JSONType, default=dict)
    tokens: Mapped[dict] = mapped_column(JSONType, default=dict)
    error: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


__all__ = ["TrajectoryNode"]
