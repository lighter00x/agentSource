"""以编程方式运行 Alembic 迁移（CLI `drugpaper db upgrade`、测试、验证脚本共用）。"""

from __future__ import annotations

import asyncio
from pathlib import Path

from alembic import command
from alembic.config import Config

ROOT = Path(__file__).resolve().parents[2]
MIGRATIONS = Path(__file__).resolve().parent / "migrations"


def alembic_config(url: str) -> Config:
    ini = ROOT / "alembic.ini"
    cfg = Config(str(ini)) if ini.exists() else Config()
    cfg.set_main_option("script_location", str(MIGRATIONS))
    cfg.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
    return cfg


def upgrade(url: str, revision: str = "head") -> None:
    command.upgrade(alembic_config(url), revision)


def downgrade(url: str, revision: str = "base") -> None:
    command.downgrade(alembic_config(url), revision)


async def upgrade_async(url: str, revision: str = "head") -> None:
    """env.py 内部会 asyncio.run，所以在已有事件循环中需要放到线程里执行。"""
    await asyncio.to_thread(upgrade, url, revision)


async def downgrade_async(url: str, revision: str = "base") -> None:
    await asyncio.to_thread(downgrade, url, revision)
