"""会话表：一条记录即前端左侧栏的一个会话（含置顶、工作区、预设、token 统计）。"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from models.base import Base, JSONType, gen_id, utcnow


class Session(Base):
    __tablename__ = "sessions"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=gen_id)
    # 归属者：多租户隔离的维度。默认 default，未开启认证时全部落在 default
    owner: Mapped[str] = mapped_column(String(64), default="default", index=True)
    title: Mapped[str] = mapped_column(String(200), default="新会话")
    # 运行态：idle / running / awaiting_input / done，与 core.memory.Session 保持一致
    status: Mapped[str] = mapped_column(String(32), default="idle")
    workspace: Mapped[str] = mapped_column(String(120), default="default")
    preset: Mapped[str] = mapped_column(String(32), default="standard")
    permission_mode: Mapped[str] = mapped_column(String(32), default="workspace_write")
    model: Mapped[str] = mapped_column(String(120), default="")
    pinned: Mapped[bool] = mapped_column(Boolean, default=False)
    # 结构化画像记忆；下划线前缀键保存运行态附加信息（artifacts/pending_question 等），
    # 这样既不新增契约外的表，也能让 load_runtime 还原出完整的 core.memory.Session
    facts: Mapped[dict] = mapped_column(JSONType, default=dict)
    summary: Mapped[str] = mapped_column(Text, default="")
    token_stats: Mapped[dict] = mapped_column(JSONType, default=dict)
    message_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


__all__ = ["Session"]
