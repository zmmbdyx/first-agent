"""自定义工具表：/api/tools 注册的工具描述与绑定方式（MCP 兼容形态）。"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from models.base import Base, JSONType, gen_id, utcnow


class CustomTool(Base):
    __tablename__ = "custom_tools"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=gen_id)
    name: Mapped[str] = mapped_column(String(80), unique=True)
    description: Mapped[str] = mapped_column(Text, default="")
    input_schema: Mapped[dict] = mapped_column(JSONType, default=dict)
    # http | python | mcp
    kind: Mapped[str] = mapped_column(String(16), default="http")
    config: Mapped[dict] = mapped_column(JSONType, default=dict)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


__all__ = ["CustomTool"]
