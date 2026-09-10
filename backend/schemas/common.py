"""公共响应模型：跨模块复用的最小结构（成功回执、通用列表）。"""
from __future__ import annotations

from typing import Generic, List, TypeVar

from pydantic import BaseModel, ConfigDict, Field

T = TypeVar("T")


class ORMModel(BaseModel):
    """允许直接从 SQLAlchemy 对象构造（服务层返回 dict 时同样适用）。"""

    model_config = ConfigDict(from_attributes=True)


class OkOut(BaseModel):
    """统一成功回执（契约要求 `{"ok": true, ...}`）。"""

    ok: bool = True


class OkIdOut(OkOut):
    id: str = ""


class ItemsOut(BaseModel, Generic[T]):
    """`{"items": [...]}` 形态的通用列表响应。"""

    items: List[T] = Field(default_factory=list)


class ErrorOut(BaseModel):
    """错误统一返回 `{"detail": "..."}`，此处仅用于文档展示。"""

    detail: str = ""
