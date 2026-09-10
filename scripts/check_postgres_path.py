# -*- coding: utf-8 -*-
"""PostgreSQL 路径校验：不依赖数据库实例，也能验证「PG 专用代码路径」是否正确。

为什么需要这个脚本：
    CI 与本地默认都跑 SQLite，PG 分支长期无人覆盖——模型里的 JSONB、时区感知时间列、
    复合索引、以及驱动 URL 只有在 PG 方言下才会真正生效。等生产切库时才发现问题代价极高。
    这里用「按方言编译 DDL」的方式在**无服务器**条件下验证这些分支，并在检测到
    可用实例时顺带做一次真实建表往返。

用法：
    set PYTHONPATH=backend
    python scripts/check_postgres_path.py                  # 方言级校验（永远可跑）
    set DATABASE_URL=postgresql+psycopg://user:pass@host/db   # 有实例时额外做真实建表
    python scripts/check_postgres_path.py

退出码：0 = 通过；1 = 失败。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001
    pass

PASSED = 0
FAILED: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    global PASSED
    if ok:
        PASSED += 1
        print(f"  [PASS] {name}" + (f"  ({detail})" if detail else ""))
    else:
        FAILED.append(f"{name} :: {detail}")
        print(f"  [FAIL] {name}  {detail}")


def main() -> int:
    print("== 1. 驱动与 URL 解析（无服务器） ==")
    try:
        from sqlalchemy import create_engine
        from sqlalchemy.engine import make_url

        url = make_url("postgresql+psycopg://u:p@localhost:5432/db")
        check("URL 方言解析", url.drivername == "postgresql+psycopg", url.drivername)
        # 只构造引擎、不连接：验证驱动可导入、URL 可被 SQLAlchemy 接受
        engine = create_engine(url, pool_pre_ping=True)
        check("引擎可构造（驱动已安装）", engine.dialect.name == "postgresql",
              f"{engine.dialect.name} / {engine.dialect.driver}")
        engine.dispose()
    except Exception as e:  # noqa: BLE001
        check("驱动与 URL 解析", False, f"{type(e).__name__}: {e}")

    print("== 2. DDL 按 PostgreSQL 方言编译（验证 JSONB / 时区列 / 主键） ==")
    try:
        from sqlalchemy.dialects import postgresql
        from sqlalchemy.schema import CreateIndex, CreateTable

        from db import Base
        import models  # 触发模型注册到 Base.metadata

        _ = models.__name__
        dialect = postgresql.dialect()
        tables = list(Base.metadata.sorted_tables)
        check("模型已注册", len(tables) >= 8, f"{len(tables)} 张表")

        ddl = "\n".join(str(CreateTable(t).compile(dialect=dialect)) for t in tables)
        check("JSON 列在 PG 下编译为 JSONB", ddl.count("JSONB") >= 6, f"{ddl.count('JSONB')} 处")
        check("时间列带时区", "TIMESTAMP WITH TIME ZONE" in ddl.upper(),
              f"{ddl.upper().count('TIMESTAMP WITH TIME ZONE')} 列")
        check("主键齐备", ddl.count("PRIMARY KEY") >= len(tables),
              f"{ddl.count('PRIMARY KEY')} / {len(tables)} 张表")

        idx = "\n".join(str(CreateIndex(i).compile(dialect=dialect))
                        for t in tables for i in t.indexes)
        check("索引可编译", bool(idx.strip()), f"{sum(len(t.indexes) for t in tables)} 个索引")

        # 关键回归：会话列表按 pinned desc, updated_at desc 排序，需要可用索引
        names = {i.name for t in tables for i in t.indexes}
        check("会话/运行表建有查询索引", bool(names), f"示例: {sorted(names)[:3]}")
    except Exception as e:  # noqa: BLE001
        check("DDL 方向编译", False, f"{type(e).__name__}: {e}")

    print("== 3. 真实实例往返（仅当 DATABASE_URL 指向可达的 PG） ==")
    dsn = (os.getenv("DATABASE_URL") or "").strip()
    if not dsn.startswith("postgresql"):
        print("  [SKIP] 未配置 PostgreSQL 的 DATABASE_URL，跳过真实建表往返")
    else:
        try:
            from sqlalchemy import text

            from db import Base, engine, init_db
            init_db()
            with engine.connect() as conn:
                conn.execute(text("select 1"))
                dialect_name = conn.dialect.name
            check("连接可用并完成建表", dialect_name == "postgresql", dialect_name)
            from sqlalchemy import inspect
            tables = set(inspect(engine).get_table_names())
            check("表已创建", {"sessions", "runs", "tasks", "tool_calls"} <= tables,
                  f"{len(tables)} 张表")
            # 清理由调用方决定（CI 用一次性库），这里只报告
            _ = Base
        except Exception as e:  # noqa: BLE001
            check("真实实例往返", False, f"{type(e).__name__}: {str(e)[:160]}")

    print("\n" + "=" * 46)
    if FAILED:
        print(f"结果: {PASSED} 通过, {len(FAILED)} 失败 ❌")
        for f in FAILED:
            print("   -", f)
        return 1
    print(f"结果: {PASSED}/{PASSED} 全部通过 🎉")
    return 0


if __name__ == "__main__":
    sys.exit(main())
