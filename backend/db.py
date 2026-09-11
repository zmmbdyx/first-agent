"""数据库引擎与会话工厂：PostgreSQL 优先、SQLite 兜底。

设计要点：
1. 惰性初始化——`import db` 绝不连接数据库，也不抛异常。测试/离线环境没有 PostgreSQL
   时只有在真正取引擎的那一刻才会回落，保证「clone 即可跑」；
2. PostgreSQL 不可达（未安装驱动 / 服务未启动）时自动切换到 `SQLITE_FALLBACK_URL`，
   并把回落事实记录在 `active_url()` 里，供 /api/health 如实上报；
3. SQLite 在多线程（FastAPI 线程池 + 队列工作线程）下必须关闭同线程校验。
"""
from __future__ import annotations

import threading
from pathlib import Path
from typing import Iterator, Optional

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session as OrmSession
from sqlalchemy.orm import sessionmaker

from config import load_config
from models.base import Base

# SQLAlchemy 2.x 推荐写法，同时兼容 1.4
try:
    from sqlalchemy.orm import DeclarativeBase as _DeclarativeBase  # noqa: F401
except ImportError:  # pragma: no cover - 老版本兜底
    _DeclarativeBase = object  # type: ignore[assignment]

_lock = threading.Lock()
_engine: Optional[Engine] = None
_session_factory: Optional[sessionmaker] = None
_active_url: str = ""
_init_done = False
_last_error: str = ""


def _prepare_sqlite_dir(url: str) -> None:
    """SQLite 落盘前先建父目录，否则首次 create_all 会因目录缺失直接失败。"""
    if not url.startswith("sqlite"):
        return
    raw = url.split("///", 1)[-1] if "///" in url else ""
    if not raw or raw == ":memory:":
        return
    try:
        Path(raw).expanduser().resolve().parent.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass


def _make_engine(url: str) -> Engine:
    _prepare_sqlite_dir(url)
    kwargs: dict = {"pool_pre_ping": True, "future": True}
    if url.startswith("sqlite"):
        # 队列工作线程与请求线程共用同一文件库，必须放开线程校验
        kwargs["connect_args"] = {"check_same_thread": False}
    return create_engine(url, **kwargs)


def _build() -> Engine:
    global _engine, _session_factory, _active_url, _last_error
    cfg = load_config()
    primary = cfg.resolved_database_url
    fallback = cfg.sqlite_fallback_url or "sqlite:///./data/pathforge.db"
    try:
        engine = _make_engine(primary)
        # create_engine 本身不连接；这里主动探测一次，把「服务未启动」提前暴露为回落
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        _active_url = primary
    except Exception as e:
        if primary.startswith("sqlite"):
            raise
        _last_error = f"{type(e).__name__}: {e}"
        if not cfg.allow_sqlite_fallback:
            # 生产语义：宁可直接失败，也不要把数据悄悄写进另一个库
            raise RuntimeError(
                f"主数据库不可达且已禁用降级（DB_FALLBACK_POLICY=deny / APP_ENV=production）："
                f"{_last_error}。请检查 DATABASE_URL；如确需回落 SQLite，"
                f"显式设置 DB_FALLBACK_POLICY=allow。") from e
        print(f"[db] 主数据库不可用（{_last_error}），已降级为 SQLite: {fallback}")
        print("[db] 生产环境请设置 DB_FALLBACK_POLICY=deny，避免连接串写错时静默写错库")
        engine = _make_engine(fallback)
        _active_url = fallback
    _engine = engine
    _session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False,
                                    expire_on_commit=False, future=True)
    return engine


def get_engine() -> Engine:
    """取引擎（首次调用才建连，失败不抛出而是回落 SQLite）。"""
    if _engine is not None:
        return _engine
    with _lock:
        if _engine is None:
            _build()
    return _engine  # type: ignore[return-value]


def SessionLocal() -> OrmSession:
    """新建 ORM 会话（调用方负责 close/commit）。"""
    if _session_factory is None:
        get_engine()
    return _session_factory()  # type: ignore[misc]


def active_url() -> str:
    """当前实际生效的连接串（已脱敏为方言+库名，供健康检查展示）。"""
    url = _active_url or load_config().resolved_database_url
    return url


def describe() -> dict:
    """健康检查用的数据库摘要：方言 + 是否发生回落。"""
    get_engine()
    cfg = load_config()
    url = active_url()
    dialect = "postgresql" if url.startswith("postgresql") else (
        "sqlite" if url.startswith("sqlite") else url.split(":", 1)[0])
    return {"dialect": dialect, "fallback": bool(_last_error),
            "configured": bool((cfg.database_url or "").strip())}


def init_db() -> None:
    """建表（幂等）+ 补列（升级路径）。任何失败都不抛出——服务必须能起来，
    由 /api/health 暴露异常。"""
    global _init_done
    try:
        from models import Base as _AllModels  # noqa: F401  确保全部模型已 import 注册
        engine = get_engine()
        _AllModels.metadata.create_all(bind=engine)
        added = ensure_columns()
        if added:
            print(f"[db] 已完成列升级: {', '.join(added)}")
        _init_done = True
    except Exception as e:
        print(f"[db] 建表失败（{type(e).__name__}: {e}），相关端点将返回明确错误")


# create_all 只建新表，**不会**给已存在的表补列；不显式 ALTER 的话，
# 老库升级后会立刻报 "no such column: sessions.owner"。
_COLUMN_UPGRADES = (
    ("sessions", "owner", "VARCHAR(64) DEFAULT 'default'"),
    ("runs", "owner", "VARCHAR(64) DEFAULT 'default'"),
    ("runs", "prompt_version", "VARCHAR(32) DEFAULT ''"),
)
_INDEX_UPGRADES = (
    ("ix_sessions_owner", "sessions", "owner"),
    ("ix_runs_owner", "runs", "owner"),
)


def ensure_columns() -> list[str]:
    """为已存在的库补齐新增列与索引（SQLite / PostgreSQL 通用）。返回实际执行的项。"""
    from sqlalchemy import inspect

    engine = get_engine()
    applied: list[str] = []
    try:
        inspector = inspect(engine)
        tables = set(inspector.get_table_names())
    except Exception as e:  # noqa: BLE001 — 探测失败不阻断启动
        print(f"[db] 列升级探测失败（{type(e).__name__}: {e}）")
        return applied

    with engine.begin() as conn:
        for table, column, ddl in _COLUMN_UPGRADES:
            if table not in tables:
                continue
            try:
                existing = {c["name"] for c in inspector.get_columns(table)}
            except Exception:  # noqa: BLE001
                continue
            if column in existing:
                continue
            try:
                conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}"))
                applied.append(f"{table}.{column}")
            except Exception as e:  # noqa: BLE001
                print(f"[db] 补列失败 {table}.{column}: {type(e).__name__}: {e}")
        for index, table, column in _INDEX_UPGRADES:
            if table not in tables:
                continue
            try:
                conn.execute(text(f"CREATE INDEX IF NOT EXISTS {index} ON {table} ({column})"))
            except Exception as e:  # noqa: BLE001 — 索引缺失只影响性能
                print(f"[db] 建索引失败 {index}: {type(e).__name__}: {e}")
    return applied


def initialized() -> bool:
    return _init_done


def ping() -> bool:
    """轻量连通性探测，供 /api/health 使用。"""
    try:
        with get_engine().connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except Exception:
        return False


def get_db() -> Iterator[OrmSession]:
    """FastAPI 依赖：每请求一个 ORM 会话，异常回滚、结束关闭。"""
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


__all__ = ["Base", "SessionLocal", "get_engine", "get_db", "init_db", "ping",
           "describe", "active_url", "initialized"]
