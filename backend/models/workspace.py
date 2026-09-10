"""工作区表：文件/Git 操作的根目录登记（每个工作区一个沙箱目录）。"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from models.base import Base, gen_id, utcnow


class Workspace(Base):
    __tablename__ = "workspaces"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=gen_id)
    name: Mapped[str] = mapped_column(String(120), unique=True)
    # 相对项目根的路径（如 ./workspaces/default）；绝对路径也允许，但必须落在工作区根内
    path: Mapped[str] = mapped_column(String(500), default="")
    description: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


__all__ = ["Workspace"]
