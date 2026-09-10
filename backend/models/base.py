"""ORM 基类与公共列类型。

要点：
- `JSONType` 让同一份模型定义在 PostgreSQL 上用 JSONB（可索引、可查询），
  在 SQLite 上退回 JSON；方言在首次建表/查询时才确定，所以用变体延迟绑定；
- `utcnow()` 一律返回带时区的 UTC 时间，避免 SQLite 丢时区后前后端解析不一致；
- `gen_id()` 与既有会话 ID 形态一致（12 位十六进制），便于与旧 data/sessions/*.json 共存。
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import DateTime, JSON
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.types import TypeEngine


class Base(DeclarativeBase):
    """全部模型的声明式基类（`Base.metadata.create_all` 即建表入口）。"""


# PostgreSQL 用 JSONB；其余方言（SQLite 等）用 JSON
JSONType: TypeEngine = JSON().with_variant(JSONB(), "postgresql")


def utcnow() -> datetime:
    """带时区的当前 UTC 时间（时间列统一 timezone=True）。"""
    return datetime.now(timezone.utc)


def gen_id(n: int = 12) -> str:
    """短随机 ID：会话/运行/任务/节点主键统一用它，长度与旧会话存档口径一致。"""
    return uuid.uuid4().hex[:n]


def iso(dt: Any) -> str:
    """时间序列化：SQLite 读回的是无时区对象，按 UTC 补齐后输出 ISO 字符串。

    前端只做字符串展示/排序，统一 UTC 才不会出现「少 8 小时」的错位。
    """
    if not isinstance(dt, datetime):
        return ""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat()


__all__ = ["Base", "JSONType", "utcnow", "gen_id", "iso", "DateTime"]
