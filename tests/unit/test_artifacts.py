"""S3.4：Artifact 版本、Work Unit、依赖与下游递归（SQLite）。并发写同一 Work Unit 的 PG 测试见 tests/integration。"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from sqlalchemy.exc import IntegrityError

from drugpaper.domain.work_units import node_dependency, split_work_unit_key, work_unit_key
from drugpaper.persistence.db import Database
from drugpaper.persistence.models import ArtifactRow, Document
from drugpaper.persistence.repos import artifacts as repo


@pytest.fixture
async def db(tmp_path: Path) -> AsyncIterator[Database]:
    d = Database(f"sqlite+aiosqlite:///{tmp_path / 'a.db'}")
    await d.create_all()
    async with d.session() as s:
        s.add(Document(id="doc_x", sha256="0" * 64))
    yield d
    await d.dispose()


def test_work_unit_key() -> None:
    assert work_unit_key("visual_classify", "doc_x/r1/p003.g000") == "visual_classify:doc_x/r1/p003.g000"
    assert work_unit_key("profile_project", "doc_x", "v1") == "profile_project:doc_x:v1"
    assert split_work_unit_key("a:doc_x/r1/p1") == ("a", "doc_x/r1/p1")
    assert node_dependency("doc_x/r1/p1", "h") == "node:doc_x/r1/p1@h"
    with pytest.raises(ValueError):
        work_unit_key("a:b", "x")


async def _new(db: Database, key: str, fp: str) -> str:
    async with db.session() as s:
        row = await repo.create_version(
            s,
            document_id="doc_x",
            artifact_type="t",
            target_ref="doc_x/r1/p1",
            work_unit_key=key,
            payload={"fp": fp},
            input_fingerprint=fp,
        )
        return row.id


async def test_versions_supersede(db: Database) -> None:
    a1 = await _new(db, "k:1", "f1")
    a2 = await _new(db, "k:1", "f2")
    async with db.session() as s:
        vs = await repo.versions(s, "k:1")
        assert [(v.version, v.is_active, v.status) for v in vs] == [
            (1, False, "superseded"),
            (2, True, "completed"),
        ]
        assert (await repo.get_active(s, "k:1")).id == a2  # type: ignore[union-attr]
        assert await repo.find_by_fingerprint(s, "k:1", "f2") is not None
        assert await repo.find_by_fingerprint(s, "k:1", "f1") is None  # 旧版本不作为缓存
        assert len(await repo.list_for_target(s, "doc_x", "doc_x/r1/p1")) == 1
        assert len(await repo.list_for_target(s, "doc_x", active_only=False)) == 2
    assert a1 != a2


async def test_unique_active_index_enforced(db: Database) -> None:
    await _new(db, "k:2", "f")
    with pytest.raises(IntegrityError):
        async with db.session() as s:
            s.add(
                ArtifactRow(
                    id="art_dup",
                    document_id="doc_x",
                    artifact_type="t",
                    target_ref="x",
                    work_unit_key="k:2",
                    is_active=True,
                )
            )


async def test_dependencies_and_downstream(db: Database) -> None:
    a = await _new(db, "a:1", "f")
    b = await _new(db, "b:1", "f")
    c = await _new(db, "c:1", "f")
    d = await _new(db, "d:1", "f")
    async with db.session() as s:
        await repo.add_dependencies(s, b, [a, node_dependency("doc_x/r1/p1", "g")])
        await repo.add_dependencies(s, c, [(b, "context")])
        await repo.add_dependencies(s, d, [c, a])
    async with db.session() as s:
        assert await repo.downstream_of(s, a) == sorted([b, c, d])
        assert await repo.downstream_of(s, node_dependency("doc_x/r1/p1", "g")) == sorted([b, c, d])
        assert await repo.downstream_of(s, d) == []
        assert set(await repo.upstream_of(s, d)) == {(c, "input"), (a, "input")}
