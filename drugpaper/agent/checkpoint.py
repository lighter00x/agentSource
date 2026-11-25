"""LangGraph checkpointer 工厂（执行方案 S2.11）。

- PostgreSQL：`langgraph-checkpoint-postgres` 的 AsyncPostgresSaver，表放在 schema `lg`（迁移 0001 创建）；
- 其他（SQLite / 未配置数据库）：内存实现（进程退出即丢失，无法跨进程恢复）。

Windows 注意：psycopg 的异步连接不支持 ProactorEventLoop，使用 PostgreSQL checkpointer 前需要
`asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())`（CLI 已处理，见 `use_selector_loop_on_windows`）。
"""

from __future__ import annotations

import asyncio
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any, cast

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import InMemorySaver
from sqlalchemy.engine import make_url

CHECKPOINT_SCHEMA = "lg"


def use_selector_loop_on_windows() -> None:
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())  # type: ignore[attr-defined,unused-ignore]


def psycopg_conninfo(database_url: str, schema: str = CHECKPOINT_SCHEMA) -> str:
    """SQLAlchemy URL → psycopg 连接串，并把 search_path 指向 checkpointer 的 schema。"""
    u = make_url(database_url).set(drivername="postgresql")
    base = u.render_as_string(hide_password=False)
    sep = "&" if "?" in base else "?"
    return f"{base}{sep}options=-csearch_path%3D{schema}"


def is_postgres_url(database_url: str) -> bool:
    return make_url(database_url).get_backend_name() == "postgresql"


@asynccontextmanager
async def open_checkpointer(database_url: str | None) -> AsyncIterator[BaseCheckpointSaver[Any]]:
    if not database_url or not is_postgres_url(database_url):
        yield InMemorySaver()
        return
    from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

    async with AsyncPostgresSaver.from_conn_string(psycopg_conninfo(database_url)) as saver:
        # setup() creates tables and indexes; serialize it across processes on this database.
        connection = cast(Any, saver.conn)
        lock_key = 0x44505035434B5035
        # Blocking advisory_lock keeps a virtual transaction open while CREATE INDEX
        # CONCURRENTLY waits for it. Poll try_lock between complete autocommit statements.
        while True:
            cursor = await connection.execute("SELECT pg_try_advisory_lock(%s)", (lock_key,))
            row = await cursor.fetchone()
            if row and (row.get("pg_try_advisory_lock") if isinstance(row, dict) else row[0]):
                break
            await asyncio.sleep(0.05)
        try:
            await saver.setup()
        finally:
            await connection.execute("SELECT pg_advisory_unlock(%s)", (lock_key,))
        yield saver
