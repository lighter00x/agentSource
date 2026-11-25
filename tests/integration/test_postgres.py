"""PostgreSQL 集成：迁移可升可降、IR 入库、FTS / trgm 检索、runs 表、Postgres checkpointer 跨进程恢复。"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
import sqlalchemy as sa

from drugpaper.agent.checkpoint import open_checkpointer
from drugpaper.agent.qa import build_ir_qa_agent
from drugpaper.agent.state import Budget
from drugpaper.cli import _with_ir_agent
from drugpaper.config.models_file import ResolvedModel
from drugpaper.config.settings import Settings
from drugpaper.docsrc.ir_source import IrDocumentSource
from drugpaper.orchestration.pipeline_graph import DocumentPipeline
from drugpaper.parsers.mineru_legacy.snapshot import ir_snapshot
from drugpaper.persistence.db import Database
from drugpaper.persistence.migrate import downgrade_async, upgrade_async
from drugpaper.persistence.models import RunRow
from drugpaper.persistence.repos.nodes import load_ir
from drugpaper.services import Services
from drugpaper.text.index import PostgresSearch
from drugpaper.tracing.db import DbTracer
from tests.fakes.fake_llm import FakeLLM, call, submit, tools
from tests.unit.ir.conftest import FIXTURE_DIR, needs_real
from tests.unit.ir.real import real_ir

pytestmark = [pytest.mark.integration, needs_real]

TABLES = {
    "lookups",
    "reader_notes",
    "full_text_results",
    "exports",
    "assays",
    "measurements",
    "figure_relations",
    "figure_reports",
    "batches",
    "compounds",
    "compound_aliases",
    "structure_candidates",
    "label_claims",
    "markush_scaffolds",
    "rgroup_assignments",
    "chemistry_conflicts",
    "skill_proposals",
    "documents",
    "parse_revisions",
    "assets",
    "nodes",
    "relations",
    "runs",
    "artifacts",
    "artifact_dependencies",
    "skill_versions",
    "capability_index",
    "capability_disclosures",
}


async def _tables(db: Database) -> set[str]:
    async with db.engine.connect() as c:
        return set(await c.run_sync(lambda sc: sa.inspect(sc).get_table_names())) - {"alembic_version"}


async def test_migrations_up_down_up(pg_url: str) -> None:
    db = Database(pg_url)
    await upgrade_async(pg_url)
    assert await _tables(db) == TABLES
    async with db.engine.connect() as c:
        cols = await c.run_sync(lambda sc: {x["name"] for x in sa.inspect(sc).get_columns("nodes")})
        assert "tsv" in cols and "user_id" in cols
        schemas = await c.run_sync(lambda sc: sa.inspect(sc).get_schema_names())
        assert "lg" in schemas
        ext = {r[0] for r in await c.execute(sa.text("SELECT extname FROM pg_extension"))}
        idx = {
            r[0] for r in await c.execute(sa.text("SELECT indexname FROM pg_indexes WHERE tablename='nodes'"))
        }
        assert "ix_nodes_tsv" in idx
        assert ("ix_nodes_text_norm_trgm" in idx) == ("pg_trgm" in ext)
    await downgrade_async(pg_url)
    assert await _tables(db) == set()
    await upgrade_async(pg_url)
    assert await _tables(db) == TABLES
    await db.dispose()


@pytest.fixture
async def ingested(pg_url: str, tmp_path: Path) -> tuple[Services, str]:
    await upgrade_async(pg_url)
    sv = Services.from_settings(Settings(database_url=pg_url, blob_root=tmp_path / "blobs", render_dpi=36))
    res = await DocumentPipeline(sv).run(offline_dir=FIXTURE_DIR)
    assert res.status == "ready", res.error
    yield sv, res.document_id  # type: ignore[misc]
    await sv.aclose()


async def test_ir_roundtrip_and_search_on_postgres(ingested: tuple[Services, str]) -> None:
    sv, doc_id = ingested
    async with sv.db.session() as s:
        ir = await load_ir(s, doc_id)
        expected = real_ir().model_copy(update={"document_id": doc_id})
        a, b = ir_snapshot(ir), ir_snapshot(expected)
        assert a["nodes"] == [{**n} for n in b["nodes"]] and a["type_counts"] == b["type_counts"]
        n_tsv = (
            await s.execute(
                sa.text("SELECT count(*) FROM nodes WHERE document_id=:d AND tsv IS NOT NULL"), {"d": doc_id}
            )
        ).scalar()
        assert n_tsv == len(ir.nodes)
        search = PostgresSearch(s, doc_id, 1)
        herg = await search.search("hERG", "fts", 5)
        assert herg and "herg" in herg[0].snippet.lower()
        d1 = await search.search("dopamine D1", "fts", 5)
        assert d1 and all(h.node_id.startswith(doc_id) for h in d1)
        fuzzy = await search.search("dopamne recepter", "trgm", 5)
        assert fuzzy
        hybrid = await search.search("allosteric modulator", "hybrid", 5)
        assert hybrid and hybrid[0].score > 0


async def test_runs_and_checkpoint_resume_across_processes(ingested: tuple[Services, str]) -> None:
    sv, doc_id = ingested
    url = sv.settings.database_url
    ref = f"{doc_id}/r1/p007.g001.c0"
    script = [
        tools(call("get_outline")),
        tools(call("grep", {"pattern": "Table 3", "node_types": ["caption"]})),
        submit("ADMET Profile for Compound 26", [{"ref": ref, "quote": "ADMET Profile for Compound 26"}]),
    ]
    src = await IrDocumentSource.load(sv.db, doc_id)

    # 进程 1：两步之后崩溃
    tracer1 = DbTracer(sv.db)
    async with open_checkpointer(url) as saver:
        agent = build_ir_qa_agent(
            src, FakeLLM([script[0], script[1], RuntimeError("killed")]), tracer1, checkpointer=saver
        )
        with pytest.raises(RuntimeError):
            await agent.ask("Table 3 的标题？", run_id="run_pg_resume")
    await tracer1.aclose()

    # 进程 2：新的 checkpointer 连接，从最后一步继续
    tracer2 = DbTracer(sv.db)
    async with open_checkpointer(url) as saver:
        fresh = FakeLLM([script[2]])
        agent = build_ir_qa_agent(src, fresh, tracer2, checkpointer=saver)
        res = await agent.resume("run_pg_resume")
    await tracer2.aclose()
    assert res.stop_reason == "answered" and len(fresh.requests) == 1 and res.tool_calls == 2
    assert res.evidence[0]["regions"][0]["page"] == 7

    async with sv.db.session() as s:
        top = await s.get(RunRow, "run_pg_resume")
        assert top is not None and top.status == "answered" and top.metrics.get("resumed") == 1
        kids = list(
            (await s.execute(sa.select(RunRow).where(RunRow.parent_run_id == "run_pg_resume"))).scalars()
        )
        assert sum(k.run_type == "tool" for k in kids) == 2
        assert sum(k.run_type == "model_call" for k in kids) == 3
        ck = (
            await s.execute(sa.text("SELECT count(*) FROM lg.checkpoints WHERE thread_id = 'run_pg_resume'"))
        ).scalar()
        assert ck and ck > 0


async def test_cli_first_question_initializes_checkpoint_without_deadlock(
    ingested: tuple[Services, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """新库首次问答不能用业务事务阻塞 checkpoint 的并发索引初始化。"""
    sv, doc_id = ingested
    ref = f"{doc_id}/r1/p007.g001.c0"

    class Client(FakeLLM):
        closed = False

        async def aclose(self) -> None:
            self.closed = True

    client = Client(
        [
            tools(call("get_node", {"node_id": ref})),
            submit("ADMET Profile for Compound 26", [{"ref": ref}]),
        ]
    )
    monkeypatch.setattr("drugpaper.cli.OpenAICompatClient", lambda config: client)
    _, result = await asyncio.wait_for(
        _with_ir_agent(
            sv.settings,
            ResolvedModel(base_url="http://unused", api_key="test", model_name="fake"),
            doc_id,
            Budget(),
            False,
            lambda agent: agent.ask("Table 3 的标题？"),
        ),
        timeout=15,
    )
    assert result.stop_reason == "answered"
    assert result.answer is not None and result.answer["citations"][0]["ref"] == ref
    assert client.closed
    async with sv.db.session() as session:
        valid = await session.execute(
            sa.text(
                "SELECT indisvalid FROM pg_index WHERE indexrelid = 'lg.checkpoints_thread_id_idx'::regclass"
            )
        )
        assert valid.scalar() is True


async def test_chemistry_skill_projection_on_postgres(ingested: tuple[Services, str]) -> None:
    from drugpaper.detection.models import RawDetection
    from drugpaper.detection.service import MoleculeDetectionService
    from drugpaper.persistence.models import NodeRow, StructureCandidateRow
    from drugpaper.tools.protocol import ToolContext
    from tests.unit.detection.test_service import FakeBackend

    sv, doc_id = ingested
    sv._detector = MoleculeDetectionService(
        FakeBackend(
            boxes=[RawDetection(bbox=(10, 20, 200, 200), confidence=0.9, class_id=0, class_name="molecule")]
        ),
        sv.settings,
    )
    bundle = await sv.skills()
    ref = f"{doc_id}/r1/p002.g001"
    tool_ctx = ToolContext(document_id=doc_id, run_id="pgchem", trace_id="trace")
    ctx = await bundle.context_for(tool_ctx, ref)
    detected = await bundle.runtime.run("molecule_detect", ref, {}, ctx)
    assert detected.status == "completed", detected.error
    local_id = detected.artifacts[0].payload["boxes"][0]["local_id"]
    molecule_ref = f"{doc_id}/r1/{local_id}"
    ctx = await bundle.context_for(tool_ctx, molecule_ref)

    async def recognize(**kwargs):
        return {"results": [{"input_index": 0, "esmi": " CCO<sep> "}]}

    ctx.tools.impls["molparser.recognize"] = recognize
    recognized = await bundle.runtime.run("molecule_recognize", molecule_ref, {}, ctx)
    assert recognized.status == "completed", recognized.error
    async with sv.db.session() as session:
        node = await session.get(NodeRow, (doc_id, 1, local_id))
        row = (
            await session.execute(
                sa.select(StructureCandidateRow).where(StructureCandidateRow.node_ref == molecule_ref)
            )
        ).scalar_one()
        assert node.status == "active" and row.payload["e_smiles_raw"] == " CCO<sep> "
