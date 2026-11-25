"""Alembic 环境（异步）。数据库地址：`-x url=...` > config 中的 sqlalchemy.url > Settings.database_url。"""

from __future__ import annotations

import asyncio

from alembic import context
from sqlalchemy.engine import Connection

from drugpaper.config.settings import get_settings
from drugpaper.persistence.db import create_engine, to_async_url
from drugpaper.persistence.models import Base

config = context.config
target_metadata = Base.metadata


def _url() -> str:
    x = context.get_x_argument(as_dictionary=True)
    url = x.get("url") or config.get_main_option("sqlalchemy.url") or get_settings().database_url
    return to_async_url(url)


def run_migrations_offline() -> None:
    context.configure(url=_url(), target_metadata=target_metadata, literal_binds=True, render_as_batch=True)
    with context.begin_transaction():
        context.run_migrations()


def _do_run(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        render_as_batch=connection.dialect.name == "sqlite",
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    engine = create_engine(_url())
    async with engine.connect() as conn:
        await conn.run_sync(_do_run)
        await conn.commit()
    await engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
