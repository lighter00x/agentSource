"""P6 durable checkpoints and projection/export on migrated PostgreSQL."""

import pytest
from sqlalchemy import select

from drugpaper.agents.reader.graph import ReaderAgent
from drugpaper.config.settings import Settings
from drugpaper.exporters.package import create_export
from drugpaper.orchestration.full_text_pipeline import FullTextPipeline
from drugpaper.persistence.migrate import upgrade_async
from drugpaper.persistence.models import (
    ArtifactRow,
    AssayRow,
    CompoundRow,
    Document,
    FigureReportRow,
    MeasurementRow,
    StructureCandidateRow,
)
from drugpaper.persistence.repos.nodes import save_ir
from drugpaper.services import Services
from tests.fakes.fake_llm import FakeLLM, text
from tests.unit.reader.test_full_text import delta, proposal  # noqa: F401 - pytest fixture import

pytestmark = pytest.mark.integration


async def test_postgres_reader_resume_and_export(pg_url, p6_env, tmp_path):
    source, ir = p6_env
    await upgrade_async(pg_url)
    sv = Services.from_settings(
        Settings(
            _env_file=None,
            database_url=pg_url,
            blob_root=tmp_path / "pgblobs",
            skills_root=tmp_path / "pgskills",
            skills_watch_enabled=False,
        )
    )
    try:
        for node in ir.nodes:
            blob = node.attributes.get("image_blob")
            if blob:
                await sv.store.put(await source.store.get(blob), "image/png")
        async with source.db.session() as origin, sv.db.session() as target:
            for cls in (
                Document,
                ArtifactRow,
                CompoundRow,
                StructureCandidateRow,
                AssayRow,
                MeasurementRow,
                FigureReportRow,
            ):
                for row in (await origin.execute(select(cls))).scalars():
                    target.add(
                        cls(**{column.key: getattr(row, column.key) for column in cls.__table__.columns})
                    )
                await target.flush()
            await save_ir(target, ir)
        interrupted = await ReaderAgent(
            sv, FakeLLM([text(delta(ir, 0).model_dump_json()), RuntimeError("crash")])
        ).read(ir.document_id)
        assert interrupted.status == "failed" and interrupted.last_page == 0
        # A new Services/Database instance restores from SQL, independent of process memory.
        other = Services.from_settings(sv.settings)
        try:
            resumed = await ReaderAgent(other, FakeLLM([text(delta(ir, 1).model_dump_json())])).read(
                ir.document_id
            )
            assert resumed.status == "completed" and resumed.notes.pages_read == [0, 1, 2, 3]
        finally:
            await other.aclose()
        output = await FullTextPipeline(sv, FakeLLM([text(proposal(ir).model_dump_json())])).run(
            ir.document_id, prepare_text=False
        )
        assert output["status"] == "completed", output
        exported = await create_export(sv, ir.document_id)
        assert "manifest.json" in exported["files"]
    finally:
        await sv.aclose()
