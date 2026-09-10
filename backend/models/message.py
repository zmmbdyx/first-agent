"""消息表：会话逐条消息（user/assistant/system），run_id 便于按轮次回溯。"""
from __future__ import annotations

from sqlalchemy import Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from models.base import Base, JSONType


class Message(Base):
    __tablename__ = "messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(String(32), ForeignKey("sessions.id", ondelete="CASCADE"),
                                            index=True)
    run_id: Mapped[str] = mapped_column(String(32), default="")
    role: Mapped[str] = mapped_column(String(16), default="user")
    content: Mapped[str] = mapped_column(Text, default="")
    # 沿用 core.memory 的 float 时间戳，落库时不做转换以便原样还原
    ts: Mapped[float] = mapped_column(Float, default=0.0)
    tokens: Mapped[dict] = mapped_column(JSONType, default=dict)


__all__ = ["Message"]
