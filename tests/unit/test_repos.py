"""S2.5：IR 入库与查询（SQLite）。PostgreSQL 上的同一流程见 tests/integration。"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from drugpaper.parsers.mineru_legacy.snapshot import ir_snapshot
from drugpaper.persistence.db import Database
from drugpaper.persistence.repos import nodes as node_repo
from drugpaper.persistence.repos.documents import create_document, get_document, list_documents, set_status
from tests.unit.ir.conftest import needs_real
from tests.unit.ir.real import DOC_ID, real_ir

pytestmark = needs_real


@pytest.fixture
async def db(tmp_path: Path) -> AsyncIterator[Database]:
    d = Database(f"sqlite+aiosqlite:///{tmp_path / 'r.db'}")
    await d.create_all()
    async with d.session() as s:
        await create_document(s, sha256="1" * 64, filename="sample.pdf", document_id=DOC_ID)
        await node_repo.save_ir(s, real_ir())
    yield d
    await d.dispose()


async def test_roundtrip_matches_snapshot(db: Database) -> None:
    async with db.session() as s:
        loaded = await node_repo.load_ir(s, DOC_ID)
    assert ir_snapshot(loaded) == ir_snapshot(real_ir())
    assert loaded.meta.doi == "10.1021/acs.jmedchem.2c00949"
    assert loaded.quality.counts() == real_ir().quality.counts()
    assert len(loaded.pages) == 17 and len(loaded.relations) == len(real_ir().relations)


async def test_document_updated(db: Database) -> None:
    async with db.session() as s:
        doc = await get_document(s, DOC_ID)
        assert doc is not None and doc.active_revision == 1 and "UCM-1306" in (doc.title or "")
        await set_status(s, DOC_ID, "ready")
        assert [d.id for d in await list_documents(s, status="ready")] == [DOC_ID]
        with pytest.raises(ValueError):
            await set_status(s, DOC_ID, "bogus")


async def test_queries(db: Database) -> None:
    async with db.session() as s:
        n = await node_repo.get_node(s, DOC_ID, "p003.g000")
        assert n is not None and n.label == "Table 1"
        kids = await node_repo.children(s, DOC_ID, "p003.g000")
        assert [k.local_id for k in kids][0] == "p003.g000.c0"
        page0 = await node_repo.nodes_on_page(s, DOC_ID, 0)
        assert "p000.b007" in {x.local_id for x in page0}
        assert all(x.node_type != "discarded" for x in page0)
        page1 = {x.local_id for x in await node_repo.nodes_on_page(s, DOC_ID, 1)}
        assert "p000.b013" in page1  # 续段 region 落在第 1 页
        win = await node_repo.context_window(s, DOC_ID, "p000.b011", before=1, after=1)
        assert [w.local_id for w in win] == ["p000.b010", "p000.b011", "p000.b013"]
        assert await node_repo.get_node(s, DOC_ID, "nope") is None


async def test_tree(db: Database) -> None:
    async with db.session() as s:
        tree = await node_repo.get_tree(s, DOC_ID)
    assert tree["id"] == "doc"
    labels = [c.get("label") for c in tree["children"] if c["type"] == "section"]
    assert "EXPERIMENTAL SECTION" in labels
    assert "text_raw" not in str(tree)


async def test_save_is_idempotent(db: Database) -> None:
    async with db.session() as s:
        await node_repo.save_ir(s, real_ir())
    async with db.session() as s:
        again = await node_repo.load_ir(s, DOC_ID)
    assert len(again.nodes) == len(real_ir().nodes)
