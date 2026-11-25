from __future__ import annotations

import json
from pathlib import Path

import pytest

from drugpaper.persistence.db import Database
from drugpaper.persistence.models import Document
from drugpaper.skills.materials import Materials
from drugpaper.skills.protocol import SkillBudget, SkillContext, ToolCaller
from drugpaper.skills.registry import SkillRegistry
from drugpaper.skills.runtime import SkillRuntime
from tests.fakes.fake_llm import FakeLLM, text

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures/skills"


@pytest.fixture
async def env(tmp_path):
    db = Database(f"sqlite+aiosqlite:///{tmp_path}/test.db")
    await db.create_all()
    async with db.session() as s:
        s.add(Document(id="doc_test", sha256="test", user_id="default"))
    registry = SkillRegistry(
        db, snapshot_root=tmp_path / "snapshots", known_tasks={"default"}, known_tools={"get_node"}
    )
    runtime = SkillRuntime(registry, db)
    ctx = SkillContext(
        "default",
        "doc_test",
        1,
        "doc_test/r1/p000.g000",
        "run_parent",
        "tr_test",
        FakeLLM([text(json.dumps({"label": "table", "confidence": 0.9}))]),
        ToolCaller(frozenset()),
        None,
        SkillBudget(2, 30),
        Materials(
            target={"ref": "doc_test/r1/p000.g000", "type": "table", "caption": "Table 1", "regions": [{}]}
        ),
    )
    yield db, registry, runtime, ctx
    await db.dispose()
