from __future__ import annotations

from pathlib import Path

import sqlalchemy as sa

from drugpaper.persistence.db import Database, is_postgres, to_async_url
from drugpaper.persistence.migrate import downgrade, upgrade
from drugpaper.persistence.models import Base

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


def _tables(url: str) -> set[str]:
    eng = sa.create_engine(url.replace("+aiosqlite", ""))
    try:
        return set(sa.inspect(eng).get_table_names()) - {"alembic_version"}
    finally:
        eng.dispose()


def test_upgrade_downgrade_upgrade(tmp_path: Path) -> None:
    url = f"sqlite+aiosqlite:///{tmp_path / 'm.db'}"
    upgrade(url)
    assert _tables(url) == TABLES
    downgrade(url)
    assert _tables(url) == set()
    upgrade(url)
    assert _tables(url) == TABLES


def test_migration_matches_models(tmp_path: Path) -> None:
    url = f"sqlite+aiosqlite:///{tmp_path / 'm.db'}"
    upgrade(url)
    eng = sa.create_engine(url.replace("+aiosqlite", ""))
    insp = sa.inspect(eng)
    for table in Base.metadata.sorted_tables:
        migrated = {c["name"] for c in insp.get_columns(table.name)}
        assert {c.name for c in table.columns} == migrated, table.name
        assert any(c["name"] == "user_id" for c in insp.get_columns(table.name))
    eng.dispose()


def test_url_helpers() -> None:
    assert to_async_url("postgresql://u:p@h/db") == "postgresql+asyncpg://u:p@h/db"
    assert to_async_url("sqlite:///x.db") == "sqlite+aiosqlite:///x.db"
    assert is_postgres("postgresql+asyncpg://h/db") and not is_postgres("sqlite:///x.db")


async def test_session_roundtrip(tmp_path: Path) -> None:
    from drugpaper.persistence.models import Document

    db = Database(f"sqlite+aiosqlite:///{tmp_path / 's.db'}")
    await db.create_all()
    async with db.session() as s:
        s.add(Document(id="doc_1", sha256="x" * 64))
    async with db.session() as s:
        doc = await s.get(Document, "doc_1")
        assert doc is not None and doc.user_id == "default" and doc.status == "uploaded"
    await db.dispose()
