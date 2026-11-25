"""异步引擎与会话。

- PostgreSQL（asyncpg）为生产数据库；SQLite（aiosqlite）用于单元测试和无数据库的本地开发；
- 每个会话开始时设置 `app.current_user_id`（P2 固定为 default，P7 启用 RLS 时直接生效）。
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from sqlalchemy import event, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from drugpaper.persistence.models import DEFAULT_USER, Base


def is_postgres(url_or_engine: str | AsyncEngine) -> bool:
    name = (
        url_or_engine.dialect.name
        if isinstance(url_or_engine, AsyncEngine)
        else make_url(url_or_engine).get_backend_name()
    )
    return name == "postgresql"


def to_async_url(url: str) -> str:
    """`postgresql://` → `postgresql+asyncpg://`；`sqlite:///` → `sqlite+aiosqlite:///`。"""
    u = make_url(url)
    if u.drivername in ("postgresql", "postgres"):
        u = u.set(drivername="postgresql+asyncpg")
    elif u.drivername == "sqlite":
        u = u.set(drivername="sqlite+aiosqlite")
    return u.render_as_string(hide_password=False)


def create_engine(url: str, **kw: Any) -> AsyncEngine:
    url = to_async_url(url)
    u = make_url(url)
    if u.get_backend_name() == "sqlite" and u.database and u.database != ":memory:":
        Path(u.database).parent.mkdir(parents=True, exist_ok=True)
    engine = create_async_engine(url, **kw)
    if engine.dialect.name == "sqlite":

        @event.listens_for(engine.sync_engine, "connect")
        def _fk_on(dbapi_conn: Any, _: Any) -> None:  # pragma: no cover - 由驱动调用
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA foreign_keys=ON")
            cur.close()

    return engine


class Database:
    """引擎 + 会话工厂。"""

    def __init__(self, url: str, **engine_kw: Any) -> None:
        self.url = to_async_url(url)
        self.engine = create_engine(self.url, **engine_kw)
        self._factory = async_sessionmaker(self.engine, expire_on_commit=False)

    @property
    def is_postgres(self) -> bool:
        return self.engine.dialect.name == "postgresql"

    @asynccontextmanager
    async def session(self, user_id: str = DEFAULT_USER) -> AsyncIterator[AsyncSession]:
        async with self._factory() as s:
            if self.is_postgres:
                await s.execute(
                    text("SELECT set_config('app.current_user_id', :uid, false)"), {"uid": user_id}
                )
            try:
                yield s
                await s.commit()
            except BaseException:
                await s.rollback()
                raise

    async def create_all(self) -> None:
        """仅用于 SQLite / 测试；PostgreSQL 请使用 Alembic 迁移。"""
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    async def dispose(self) -> None:
        await self.engine.dispose()
