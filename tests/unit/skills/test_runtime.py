from __future__ import annotations

import asyncio
import json
import shutil

import pytest
from sqlalchemy import select

from drugpaper.persistence.models import ArtifactRow, RunRow, SkillProposalRow
from drugpaper.persistence.repos.artifacts import downstream_of, versions
from drugpaper.skills.runtime import SkillRuntime, input_fingerprint
from tests.fakes.fake_llm import FakeLLM, text
from tests.unit.skills.conftest import FIXTURES


async def test_prompt_execution_cache_force_and_dependencies(env):
    db, registry, runtime, ctx = env
    await registry.register(FIXTURES / "demo_prompt")
    ctx.materials.upstream_versions = {"art_upstream": 2}
    ctx.materials.node_hash = "geometry_v1"
    first = await runtime.run("demo_prompt", ctx.target_ref, {"node_ref": ctx.target_ref}, ctx)
    assert first.status == "completed", first.error
    hit = await runtime.run("demo_prompt", ctx.target_ref, {"node_ref": ctx.target_ref}, ctx)
    assert hit.cached and hit.artifacts[0].artifact_id == first.artifacts[0].artifact_id
    ctx.model = FakeLLM([text(json.dumps({"label": "table", "confidence": 0.8}))])
    second = await runtime.run("demo_prompt", ctx.target_ref, {"node_ref": ctx.target_ref}, ctx, force=True)
    assert second.artifacts[0].version == 2
    async with db.session() as s:
        rows = await versions(s, "demo_prompt:" + ctx.target_ref)
        assert [r.is_active for r in rows] == [False, True]
        assert rows[0].status == "superseded"
        assert await downstream_of(s, "art_upstream") == sorted(r.id for r in rows)


@pytest.mark.parametrize(
    "mode,fragment",
    [
        ("bad_schema", "is not of type"),
        ("bad_fact", "fact type not allowed"),
        ("tool", "未声明工具"),
        ("slow", "TimeoutError"),
        ("node", "子节点类型"),
    ],
)
async def test_atomic_rejection_and_failed_run(env, tmp_path, mode, fragment):
    db, registry, runtime, ctx = env
    root = tmp_path / "demo_python"
    shutil.copytree(FIXTURES / "demo_python", root)
    if mode == "slow":
        import yaml

        manifest = yaml.safe_load((root / "manifest.yaml").read_text())
        manifest["budgets"]["max_wall_time_s"] = 0.01
        (root / "manifest.yaml").write_text(yaml.safe_dump(manifest))
    await registry.register(root)
    result = await runtime.run("demo_python", ctx.target_ref, {"text": "one two", "mode": mode}, ctx)
    assert result.status == "failed" and fragment in result.error
    async with db.session() as s:
        assert not list((await s.execute(select(ArtifactRow))).scalars())
        row = await s.get(RunRow, result.run_id)
        assert row.status == "failed" and row.error
        assert not list((await s.execute(select(SkillProposalRow))).scalars())


async def test_fact_proposals_commit_and_event_after_transaction(env):
    db, registry, _, ctx = env
    await registry.register(FIXTURES / "demo_python")
    seen = []

    async def event(name, payload):
        async with db.session() as s:
            row = await s.get(ArtifactRow, payload["artifact_ids"][0])
            assert row
        seen.append(name)

    runtime = SkillRuntime(registry, db, event_sink=event)
    outcome = await runtime.run("demo_python", ctx.target_ref, {"text": "one two", "mode": "facts"}, ctx)
    assert outcome.status == "completed" and seen == ["artifact.updated"]
    async with db.session() as s:
        proposals = list((await s.execute(select(SkillProposalRow))).scalars())
        assert proposals[0].payload["fact_type"] == "measurement"


async def test_locked_result_is_rejected(env):
    db, registry, _, ctx = env
    await registry.register(FIXTURES / "demo_python")

    async def locked(*args):
        return True

    result = await SkillRuntime(registry, db, lock_check=locked).run(
        "demo_python", ctx.target_ref, {"text": "x"}, ctx
    )
    assert result.status == "failed" and "锁定" in result.error


async def test_concurrent_runs_have_one_active_version(env):
    db, registry, runtime, ctx = env
    await registry.register(FIXTURES / "demo_python")
    outcomes = await asyncio.gather(
        *(runtime.run("demo_python", ctx.target_ref, {"text": str(i)}, ctx, force=True) for i in range(6))
    )
    assert all(o.status == "completed" for o in outcomes)
    async with db.session() as s:
        rows = await versions(s, "demo_python:" + ctx.target_ref)
        assert len(rows) == 6 and sum(r.is_active for r in rows) == 1
        assert [r.version for r in rows] == list(range(1, 7))


async def test_fingerprint_changes_for_inputs_upstreams_prompt_and_model(env):
    _, registry, _, ctx = env
    await registry.register(FIXTURES / "demo_prompt")
    skill = await registry.resolve("demo_prompt")
    baseline = input_fingerprint(skill, {"x": 1}, ctx)
    assert baseline != input_fingerprint(skill, {"x": 2}, ctx)
    ctx.materials.upstream_versions = {"art_a": 2}
    assert baseline != input_fingerprint(skill, {"x": 1}, ctx)
    ctx.materials.upstream_versions = {}
    ctx.extras["model_spec"] = {"model": "different"}
    assert baseline != input_fingerprint(skill, {"x": 1}, ctx)


async def test_python_model_budget_and_whitelist(env, tmp_path):
    _, registry, runtime, ctx = env
    root = tmp_path / "demo_python"
    shutil.copytree(FIXTURES / "demo_python", root)
    (root / "scripts/main.py").write_text(
        "from drugpaper.gateway.base import ChatRequest\n"
        "from drugpaper.skills.protocol import SkillResult, ArtifactDraft\n"
        "async def run(inputs,ctx):\n"
        ' if inputs.get("mode") == "tool":\n'
        '  await ctx.model.chat(ChatRequest(messages=[]),task="default")\n'
        ' return SkillResult(artifacts=[ArtifactDraft(artifact_type="word_count",payload={"words":len(inputs["text"].split())})])\n'
    )
    import yaml

    manifest = yaml.safe_load((root / "manifest.yaml").read_text())
    manifest["model_tasks"] = {"main": "default"}
    (root / "manifest.yaml").write_text(yaml.safe_dump(manifest))
    await registry.register(root)
    result = await runtime.run("demo_python", ctx.target_ref, {"text": "x", "mode": "tool"}, ctx)
    assert result.status == "failed" and "max_model_calls" in result.error


async def test_node_geometry_and_validators(env, tmp_path):
    _, registry, runtime, ctx = env
    root = tmp_path / "demo_python"
    shutil.copytree(FIXTURES / "demo_python", root)
    import yaml

    manifest = yaml.safe_load((root / "manifest.yaml").read_text())
    manifest["produces"]["node_types"] = ["molecule"]
    (root / "manifest.yaml").write_text(yaml.safe_dump(manifest))
    await registry.register(root)
    result = await runtime.run("demo_python", ctx.target_ref, {"text": "x", "mode": "node"}, ctx)
    assert result.status == "completed", result.error
    ctx.materials.target["regions"] = []
    result = await runtime.run("demo_python", ctx.target_ref, {"text": "x", "mode": "node"}, ctx, force=True)
    assert result.status == "failed" and "region" in result.error


async def test_schema_and_custom_validator_rejection_rolls_back(env, tmp_path):
    db, registry, _, ctx = env
    root = tmp_path / "demo_python"
    shutil.copytree(FIXTURES / "demo_python", root)
    import yaml

    manifest = yaml.safe_load((root / "manifest.yaml").read_text())
    manifest["validators"] = ["schema", "custom"]
    (root / "manifest.yaml").write_text(yaml.safe_dump(manifest))
    await registry.register(root)
    calls = []

    async def validator(skill, result, context):
        calls.append(skill.name)
        raise ValueError("custom validation rejected")

    result = await SkillRuntime(registry, db, validators={"custom": validator}).run(
        "demo_python", ctx.target_ref, {"text": "x"}, ctx
    )
    assert result.status == "failed" and calls == ["demo_python"]
    async with db.session() as s:
        assert not list((await s.execute(select(ArtifactRow))).scalars())


async def test_python_package_supports_relative_imports_and_dataclasses(env, tmp_path):
    _, registry, runtime, ctx = env
    root = tmp_path / "demo_python"
    shutil.copytree(FIXTURES / "demo_python", root)
    (root / "scripts/helper.py").write_text(
        "from dataclasses import dataclass\n@dataclass\nclass Counter:\n words: int\n"
    )
    (root / "scripts/main.py").write_text(
        'from .helper import Counter\nfrom drugpaper.skills.protocol import SkillResult,ArtifactDraft\nasync def run(inputs,ctx):\n c=Counter(len(inputs["text"].split()))\n return SkillResult(artifacts=[ArtifactDraft(artifact_type="word_count",payload={"words":c.words})])\n'
    )
    await registry.register(root)
    outcome = await runtime.run("demo_python", ctx.target_ref, {"text": "x y"}, ctx)
    assert outcome.artifacts[0].payload == {"words": 2}, outcome.error


async def test_detecting_skill_requires_backend_version_before_artifact_cache(env, tmp_path):
    import yaml

    db, registry, runtime, ctx = env
    root = tmp_path / "demo_python"
    shutil.copytree(FIXTURES / "demo_python", root)
    manifest = yaml.safe_load((root / "manifest.yaml").read_text())
    manifest["tools"] = ["molecule.detect"]
    (root / "manifest.yaml").write_text(yaml.safe_dump(manifest))
    registry.known_tools.add("molecule.detect")
    await registry.register(root)
    first = await runtime.run("demo_python", ctx.target_ref, {"text": "same"}, ctx)
    second = await runtime.run("demo_python", ctx.target_ref, {"text": "same"}, ctx)
    assert first.status == second.status == "completed" and not second.cached
    ctx.extras["tool_fingerprints"] = {"molecule.detect": "weights_v1+params"}
    third = await runtime.run("demo_python", ctx.target_ref, {"text": "same"}, ctx)
    hit = await runtime.run("demo_python", ctx.target_ref, {"text": "same"}, ctx)
    assert not third.cached and hit.cached
    ctx.extras["tool_fingerprints"] = {"molecule.detect": "weights_v2+params"}
    changed = await runtime.run("demo_python", ctx.target_ref, {"text": "same"}, ctx)
    assert not changed.cached


@pytest.mark.parametrize("quality,status", [("needs_review", "needs_review"), ("failed", "failed")])
async def test_quality_status_reaches_artifact_and_tool_cache(env, tmp_path, quality, status):
    db, registry, runtime, ctx = env
    root = tmp_path / "demo_python"
    shutil.copytree(FIXTURES / "demo_python", root)
    (root / "scripts/main.py").write_text(
        "from drugpaper.skills.protocol import SkillResult,ArtifactDraft,SkillQuality\n"
        "async def run(inputs,ctx):\n"
        f" return SkillResult(artifacts=[ArtifactDraft(artifact_type='word_count',payload={{'words':3}})],quality=SkillQuality(status='{quality}'))\n"
    )
    await registry.register(root)
    outcome = await runtime.run("demo_python", ctx.target_ref, {"text": "one two three"}, ctx)
    assert outcome.status == "completed" and outcome.artifacts[0].status == status
    again = await runtime.run("demo_python", ctx.target_ref, {"text": "one two three"}, ctx)
    assert again.cached is (status != "failed")
    assert again.artifacts[0].status == status


async def test_post_commit_notification_failure_preserves_delivery(env):
    db, registry, _, ctx = env
    await registry.register(FIXTURES / "demo_python")

    async def unavailable(*args):
        raise ConnectionError("notification service unavailable")

    runtime = SkillRuntime(registry, db, event_sink=unavailable)
    first = await runtime.run("demo_python", ctx.target_ref, {"text": "one two three"}, ctx)
    assert first.status == "completed"
    async with db.session() as session:
        assert (await session.get(RunRow, first.run_id)).status == "completed"
    again = await runtime.run("demo_python", ctx.target_ref, {"text": "one two three"}, ctx)
    assert again.cached and again.artifacts[0].artifact_id == first.artifacts[0].artifact_id


async def test_failed_quality_cannot_project_facts(env, tmp_path):
    db, registry, runtime, ctx = env
    root = tmp_path / "demo_python"
    shutil.copytree(FIXTURES / "demo_python", root)
    (root / "scripts/main.py").write_text(
        "from drugpaper.skills.protocol import SkillResult,ArtifactDraft,SkillQuality,FactProposal\n"
        "async def run(inputs,ctx):\n"
        " failed=inputs.get('mode')=='facts'\n"
        " return SkillResult(artifacts=[ArtifactDraft(artifact_type='word_count',payload={'words':3})],quality=SkillQuality(status='failed' if failed else 'ok'),fact_proposals=[FactProposal(fact_type='measurement',payload={'value':123})] if failed else [])\n"
    )
    await registry.register(root)
    result = await runtime.run("demo_python", ctx.target_ref, {"text": "one two three", "mode": "facts"}, ctx)
    assert result.status == "failed" and "不得提交" in result.error
    async with db.session() as session:
        assert not list((await session.execute(select(ArtifactRow))).scalars())
        assert not list((await session.execute(select(SkillProposalRow))).scalars())
