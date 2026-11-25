"""Copy isolated verification data into a new PostgreSQL database; no production writes."""

import asyncio
import json

from sqlalchemy import select, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine

from drugpaper.config.settings import Settings
from drugpaper.persistence.db import Database
from drugpaper.persistence.migrate import upgrade_async
from drugpaper.persistence.models import Base
from scripts.ingest_phase5_data import ROOT


async def main():
    name = "drugpaper_p5_verify_20261002"
    base = make_url(Settings().database_url)
    if base.get_backend_name() != "postgresql":
        raise ValueError("Configured PostgreSQL required")
    url = base.set(database=name).render_as_string(hide_password=False)
    admin = create_async_engine(base.render_as_string(hide_password=False), isolation_level="AUTOCOMMIT")
    async with admin.connect() as connection:
        exists = await connection.scalar(
            text("SELECT 1 FROM pg_database WHERE datname=:name"), {"name": name}
        )
        if not exists:
            await connection.execute(text(f'CREATE DATABASE "{name}"'))
    await admin.dispose()
    await upgrade_async(url)
    source = Database(f"sqlite+aiosqlite:///{ROOT / 'phase5.db'}")
    destination = Database(url)
    mapping = {m.local_table.name: m.class_ for m in Base.registry.mappers}
    try:
        for table in Base.metadata.sorted_tables:
            model = mapping[table.name]
            async with destination.session() as session:
                if await session.scalar(select(model).limit(1)) is not None:
                    continue
                async with source.session() as old:
                    rows = list((await old.execute(select(model))).scalars())
                    values = [{c.key: getattr(row, c.key) for c in table.columns} for row in rows]
                for value in values:
                    session.add(model(**value))
            print(table.name, len(values), flush=True)
        async with destination.session() as session:
            await session.execute(
                text(
                    "SELECT setval(pg_get_serial_sequence('artifact_dependencies','id'), GREATEST(COALESCE(MAX(id),0),1), MAX(id) IS NOT NULL) FROM artifact_dependencies"
                )
            )
        (ROOT / "database.json").write_text(json.dumps({"backend": "postgresql", "database": name}, indent=2))
    finally:
        await source.dispose()
        await destination.dispose()


if __name__ == "__main__":
    asyncio.run(main())
