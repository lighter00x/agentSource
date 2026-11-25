"""真实 PostgreSQL 的多 Runtime 提交与唯一 active 版本。"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from sqlalchemy import select

from drugpaper.persistence.db import Database
from drugpaper.persistence.migrate import upgrade_async
from drugpaper.persistence.models import ArtifactRow, Document
from drugpaper.skills.protocol import SkillBudget, SkillContext, ToolCaller
from drugpaper.skills.registry import SkillRegistry
from drugpaper.skills.runtime import SkillRuntime
from tests.fakes.fake_llm import FakeLLM

pytestmark = pytest.mark.integration


async def test_two_runtimes_create_only_one_active_work_unit(pg_url, tmp_path):
    await upgrade_async(pg_url)
    db = Database(pg_url)
    try:
        async with db.session() as s:
            s.add(Document(id="doc_pg_skill", sha256="test"))
        registry = SkillRegistry(db, snapshot_root=tmp_path / "snapshots")
        await registry.register(Path("tests/fixtures/skills/demo_python"))
        ctx = SkillContext(
            "default",
            "doc_pg_skill",
            1,
            "doc_pg_skill/r1/p000.g000",
            "parent",
            "tr",
            FakeLLM([]),
            ToolCaller(frozenset()),
            None,
            SkillBudget(0, 5),
        )
        runtimes = [SkillRuntime(registry, db), SkillRuntime(registry, db)]
        results = await asyncio.gather(
            *(
                runtimes[i % 2].run("demo_python", ctx.target_ref, {"text": str(i)}, ctx, force=True)
                for i in range(8)
            )
        )
        assert all(r.status == "completed" for r in results), [r.error for r in results]
        async with db.session() as s:
            rows = list((await s.execute(select(ArtifactRow))).scalars())
            assert len(rows) == 8 and sum(r.is_active for r in rows) == 1
            assert sorted(r.version for r in rows) == list(range(1, 9))
    finally:
        await db.dispose()
