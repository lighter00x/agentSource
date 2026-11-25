"""Authorized P5 test corpus ingestion into isolated assets/database."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from drugpaper.config.settings import Settings
from drugpaper.orchestration.pipeline_graph import DocumentPipeline
from drugpaper.persistence.migrate import upgrade_async
from drugpaper.services import Services

ROOT = Path("samples/outputs/phase-5/20261002").resolve()


def settings_for(root: Path = ROOT) -> Settings:
    from sqlalchemy.engine import make_url

    base = Settings()
    marker = root / "database.json"
    database_url = f"sqlite+aiosqlite:///{root / 'phase5.db'}"
    if marker.exists():
        database_name = json.loads(marker.read_text())["database"]
        database_url = (
            make_url(base.database_url).set(database=database_name).render_as_string(hide_password=False)
        )
    return base.model_copy(
        update={
            "database_url": database_url,
            "blob_root": root / "blobs",
            "skills_root": root / "skills",
            "runs_dir": root / "runs",
            "redis_url": base.redis_url,
        }
    )


async def main() -> None:
    ROOT.mkdir(parents=True, exist_ok=True)
    settings = settings_for()
    await upgrade_async(settings.database_url)
    sv = Services.from_settings(settings)
    gate = asyncio.Semaphore(2)
    results = []
    fixture = await DocumentPipeline(sv).run(offline_dir=Path("tests/fixtures/jmedchem_2c00949"))
    results.append({"file": "jmedchem_2c00949", "result": fixture.model_dump()})

    async def one(file: Path) -> None:
        async with gate:
            print("parsing", file.name, flush=True)
            result = await DocumentPipeline(sv).run(
                pdf=await asyncio.to_thread(file.read_bytes), filename=file.name
            )
            results.append({"file": file.name, "result": result.model_dump()})
            print(file.name, result.status, flush=True)
            await asyncio.to_thread(
                (ROOT / "ingest.json").write_text, json.dumps(results, ensure_ascii=False, indent=2)
            )

    try:
        files = await asyncio.to_thread(lambda: sorted(Path("tests/data").glob("*.pdf")))
        await asyncio.gather(*(one(p) for p in files))
    finally:
        await asyncio.to_thread(
            (ROOT / "ingest.json").write_text, json.dumps(results, ensure_ascii=False, indent=2)
        )
        await sv.aclose()


if __name__ == "__main__":
    asyncio.run(main())
