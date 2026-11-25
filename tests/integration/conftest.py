"""集成测试：需要真实 PostgreSQL。

数据库来源（按优先级）：
1. 环境变量 `TEST_DATABASE_URL`（例如 `make up` 后的 `postgresql+asyncpg://drugpaper:drugpaper@localhost:5432/drugpaper_test`）；
2. 嵌入式 PostgreSQL（dev 依赖 `pgserver`，自带 pgvector，但没有 pg_trgm —— trgm 检索会走 Python 回退）；
都不可用时整体跳过。
"""

from __future__ import annotations

import asyncio
import os
import sys
import tempfile
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest

from tests.unit.reader.test_full_text import full_env as p6_env  # noqa: F401 - shared P6 seed fixture

pytestmark = pytest.mark.integration


def pytest_asyncio_loop_factories(config: pytest.Config, item: pytest.Item) -> dict[str, object]:
    # psycopg 的异步连接（LangGraph Postgres checkpointer）不支持 Windows 的 ProactorEventLoop
    if sys.platform == "win32" and "integration" in item.keywords:
        return {"selector": asyncio.SelectorEventLoop}
    return {"default": asyncio.new_event_loop}


@pytest.fixture(scope="session")
def pg_server_url() -> Iterator[str]:
    env = os.environ.get("TEST_DATABASE_URL")
    if env:
        yield env
        return
    try:
        import pgserver  # type: ignore[import-untyped]
    except ImportError:
        pytest.skip("没有 TEST_DATABASE_URL，也没有安装 pgserver")
    data_dir = Path(tempfile.gettempdir()) / "drugpaper_pgserver"
    srv = pgserver.get_server(str(data_dir), cleanup_mode="stop")
    yield srv.get_uri()


@pytest.fixture
def pg_url(pg_server_url: str) -> str:
    """每个测试一个全新的数据库。"""
    import sqlalchemy as sa

    name = f"dp_test_{uuid.uuid4().hex[:10]}"
    admin = sa.create_engine(
        pg_server_url.replace("+asyncpg", "").replace("postgresql://", "postgresql+psycopg://"),
        isolation_level="AUTOCOMMIT",
    )
    with admin.connect() as c:
        c.execute(sa.text(f'CREATE DATABASE "{name}"'))
    admin.dispose()
    base = sa.engine.make_url(pg_server_url).set(drivername="postgresql+asyncpg", database=name)
    return base.render_as_string(hide_password=False)


@pytest.fixture
def mark_integration() -> None:
    return None
