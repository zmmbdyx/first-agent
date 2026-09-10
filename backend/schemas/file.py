"""文件 / Git / 业务资产相关响应模型（契约 2.6 / 2.7 节）。"""
from __future__ import annotations

from typing import Any, Dict, List, Literal

from pydantic import BaseModel, Field

NodeType = Literal["file", "dir"]
ContentType = Literal["text", "markdown", "image", "pdf", "binary"]


class FileNode(BaseModel):
    name: str
    path: str
    type: NodeType = "file"
    size: int = 0
    mtime: float = 0.0
    children: List["FileNode"] = Field(default_factory=list)


class FileTreeOut(BaseModel):
    root: str = ""
    nodes: List[FileNode] = Field(default_factory=list)


class FileContentOut(BaseModel):
    path: str
    type: ContentType = "text"
    content: str = ""
    language: str = ""
    size: int = 0
    truncated: bool = False


class GitChange(BaseModel):
    path: str
    status: str = "M"      # M/A/D/R/??
    staged: bool = False


class GitStatusOut(BaseModel):
    is_repo: bool = False
    branch: str = ""
    changes: List[GitChange] = Field(default_factory=list)


class GitStageIn(BaseModel):
    workspace: str = ""
    paths: List[str] = Field(default_factory=list)


class GitStageOut(BaseModel):
    ok: bool = True
    changes: List[GitChange] = Field(default_factory=list)


class ResumeOut(BaseModel):
    name: str
    size: int = 0
    mtime: float = 0.0
    path: str = ""
    url: str = ""


class AssetListOut(BaseModel):
    items: List[Dict[str, Any]] = Field(default_factory=list)


class ModelSelectIn(BaseModel):
    model: str = ""


class ModelsOut(BaseModel):
    models: List[str] = Field(default_factory=list)
    current: str = ""
    provider: str = ""


class HealthOut(BaseModel):
    """健康检查用展示模型；实际响应体另含 tools/tool_costs/cache 等运行期附加字段。"""

    status: str = "ok"
    provider: str = ""
    model: str = ""
    tools: List[str] = Field(default_factory=list)
    db: Dict[str, Any] = Field(default_factory=dict)
    redis: Dict[str, Any] = Field(default_factory=dict)
    vector_store: Dict[str, Any] = Field(default_factory=dict)
    sandbox: Dict[str, Any] = Field(default_factory=dict)
    encrypted_storage: bool = False
    cache: Dict[str, Any] = Field(default_factory=dict)
