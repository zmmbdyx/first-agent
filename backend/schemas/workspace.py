"""工作区请求/响应模型（契约 2.5 节）。

`WorkspaceOut`：id,name,path,description,created_at,updated_at,exists,file_count
"""
from __future__ import annotations

from typing import List

from pydantic import BaseModel, Field


class WorkspaceCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=120)
    path: str = ""
    description: str = ""


class WorkspaceUpdate(BaseModel):
    name: str = ""
    path: str = ""
    description: str = ""


class WorkspaceOut(BaseModel):
    id: str
    name: str
    path: str
    description: str = ""
    created_at: str = ""
    updated_at: str = ""
    exists: bool = False
    file_count: int = 0


class WorkspaceListOut(BaseModel):
    items: List[WorkspaceOut] = Field(default_factory=list)
