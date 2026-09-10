"""全局配置：pydantic-settings 统一读取环境变量与 .env。

设计要点：
1. 所有敏感项（API Key、数据库 / Redis / 向量库连接串、沙箱参数）**只从环境变量读取**，代码内零硬编码；
2. 未配置 LLM 凭据时自动降级为离线 mock 模式，保证 clone 后开箱即跑；
3. 字段名与旧版 `config.Config` 保持兼容（provider / api_key / base_url / model / models /
   temperature / max_react_steps / ...），既有业务核心 core/agent.py、core/llm.py、
   core/planner.py、core/tools/* 无需改动即可继续工作。
"""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# 布局：<repo>/backend/config.py → BACKEND_DIR=<repo>/backend，PROJECT_ROOT=<repo>
BACKEND_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = BACKEND_DIR.parent
ROOT = PROJECT_ROOT  # 运行时数据（data/）仍位于仓库根，与 .gitignore 约定一致

# .env 查找顺序：backend/.env、项目根 .env、项目根 .env.local（后者覆盖前者）
_ENV_FILES = (BACKEND_DIR / ".env", PROJECT_ROOT / ".env", PROJECT_ROOT / ".env.local")

_NUM_DEFAULTS = {
    "max_react_steps": 5, "llm_max_retries": 3, "tool_max_retries": 2, "max_tasks": 6,
    "task_timeout": 300, "sandbox_timeout": 300, "sandbox_max_output": 65536, "api_port": 8000,
    "max_concurrent_runs": 2,
}


def _csv(value) -> list[str]:
    if isinstance(value, (list, tuple)):
        return [str(v).strip() for v in value if str(v).strip()]
    return [p.strip() for p in str(value or "").replace("\n", ",").split(",") if p.strip()]


class Settings(BaseSettings):
    """全部运行期配置。字段名即环境变量名（大小写不敏感）。"""

    model_config = SettingsConfigDict(
        env_file=tuple(str(p) for p in _ENV_FILES),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # ---------------- 应用 ----------------
    app_name: str = "PATHFORGE"
    app_env: str = "development"
    api_host: str = "127.0.0.1"
    api_port: int = 8000
    cors_origins: list[str] = Field(
        default_factory=lambda: ["http://127.0.0.1:5173", "http://localhost:5173"])

    # ---------------- LLM ----------------
    llm_provider: str = ""
    llm_api_key: str = ""
    llm_base_url: str = ""
    model: str = ""                       # LLM_MODEL
    models: list[str] = Field(default_factory=list)  # LLM_MODELS（可切换列表）
    fallback_model: str = ""              # LLM_FALLBACK_MODEL
    temperature: float = 0.3              # LLM_TEMPERATURE
    max_react_steps: int = 5              # MAX_REACT_STEPS
    llm_max_retries: int = 3              # LLM_MAX_RETRIES
    tool_max_retries: int = 2             # TOOL_MAX_RETRIES
    max_tasks: int = 6                    # MAX_TASKS
    task_timeout: int = 300               # TASK_TIMEOUT
    privacy_mode: bool = False
    search_api_key: str = ""              # SEARCH_API_KEY（可选联网检索增强）

    # ---------------- 数据库 ----------------
    database_url: str = ""
    sqlite_fallback_url: str = "sqlite:///./data/pathforge.db"

    # ---------------- Redis / 向量库 ----------------
    redis_url: str = ""
    vector_store_url: str = ""
    vector_store_path: str = "./data/vectors"
    embedding_model: str = ""

    # ---------------- 沙箱 / 工作区 ----------------
    sandbox_enabled: bool = True
    sandbox_timeout: int = 300
    sandbox_max_output: int = 65536
    workspace_root: str = "./workspaces"
    allow_full_access: bool = False

    # ---------------- 数据加密 ----------------
    data_key: str = ""

    # ---------------- 运行期（由 RunService 按请求覆盖，不落环境变量） ----------------
    max_concurrent_runs: int = 2          # MAX_CONCURRENT_RUNS：同时在跑的 run 上限
    preset_id: str = "standard"           # 本次运行所用预设（运行期副本字段）
    reasoning_effort: str = "medium"      # 本次运行推理强度（运行期副本字段）

    # ---------------- 兼容字段（旧代码读取的属性名） ----------------
    provider: str = ""
    api_key: str = ""
    base_url: str = ""
    reports_dir: Path = Field(default_factory=lambda: ROOT / "data" / "reports")
    sessions_dir: Path = Field(default_factory=lambda: ROOT / "data" / "sessions")

    # ---------------- 校验 ----------------
    @field_validator("models", "cors_origins", mode="before")
    @classmethod
    def _split_csv(cls, v):
        return _csv(v)

    @field_validator("temperature", mode="before")
    @classmethod
    def _safe_float(cls, v):
        try:
            return float(v)
        except (TypeError, ValueError):
            return 0.3

    @field_validator(*_NUM_DEFAULTS, mode="before")
    @classmethod
    def _safe_int(cls, v):
        try:
            return int(float(v))
        except (TypeError, ValueError):
            return 0

    def model_post_init(self, __context) -> None:  # noqa: D105
        # 数字护栏：配置写错（如 MAX_REACT_STEPS=abc）不得让服务起不来
        for key, dflt in _NUM_DEFAULTS.items():
            if not getattr(self, key):
                setattr(self, key, dflt)
        self.max_react_steps = max(1, self.max_react_steps)
        self.llm_max_retries = max(1, self.llm_max_retries)
        self.tool_max_retries = max(0, self.tool_max_retries)
        self.max_tasks = max(1, self.max_tasks)

        # provider 归一化：不识别任何厂商品牌，只看凭据是否完整
        provider = (self.llm_provider or "").strip().lower()
        if not provider or provider == "mock":
            provider = "openai-compatible" if (self.llm_api_key and self.llm_base_url
                                               and self.model) else "mock"
        if provider != "mock" and not (self.llm_api_key and self.llm_base_url and self.model):
            print("[config] LLM 凭据不完整（需同时提供 LLM_API_KEY / LLM_BASE_URL / LLM_MODEL），"
                  "已降级为离线 mock 模式")
            provider = "mock"
        self.provider = provider

        # 兼容别名
        self.api_key = self.llm_api_key
        self.base_url = self.llm_base_url

        # 可切换模型列表：并入当前模型与备用模型并去重
        merged = list(self.models)
        for m in (self.model, self.fallback_model):
            if m and m not in merged:
                merged.insert(0, m)
        self.models = merged

        # 目录准备（失败不阻断启动）
        for d in (self.reports_dir, self.reports_dir / "charts", self.sessions_dir,
                  self.data_path / "uploads", self.data_path / "cache", self.workspace_path):
            try:
                d.mkdir(parents=True, exist_ok=True)
            except OSError:
                pass

        if self.data_key:
            os.environ.setdefault("DATA_KEY", self.data_key)

    # ---------------- 派生属性 ----------------
    @property
    def workspace_path(self) -> Path:
        p = Path(self.workspace_root)
        return p if p.is_absolute() else (ROOT / p)

    @property
    def data_path(self) -> Path:
        return ROOT / "data"

    @property
    def resolved_database_url(self) -> str:
        """生产用 PostgreSQL（DATABASE_URL），未配置时回落 SQLite，保证开箱即跑。"""
        return (self.database_url or "").strip() or self.sqlite_fallback_url

    @property
    def is_postgres(self) -> bool:
        return self.resolved_database_url.startswith("postgresql")

    @property
    def llm_ready(self) -> bool:
        return self.provider != "mock"


def to_root_relative(path) -> str:
    """把本地路径统一转成「相对项目根 + 正斜杠」的形式，供 /api/files/report 与前端引用。"""
    p = Path(path)
    try:
        p = p.resolve()
    except OSError:
        return p.as_posix()
    try:
        return p.relative_to(ROOT).as_posix()
    except ValueError:
        return p.as_posix()  # 不在项目根下（如用户自定义目录）时退回绝对路径


@lru_cache(maxsize=1)
def _cached() -> Settings:
    try:
        return Settings()
    except Exception as e:  # 环境变量非法也必须能启动
        print(f"[config] 环境变量解析失败（{type(e).__name__}: {e}），使用默认配置")
        return Settings(_env_file=None)


def load_config() -> Settings:
    """读取配置（进程内缓存）。"""
    return _cached()


# 旧代码 `from config import Config` 的类型注解兼容
Config = Settings

__all__ = ["Settings", "Config", "load_config", "ROOT", "BACKEND_DIR", "PROJECT_ROOT"]
