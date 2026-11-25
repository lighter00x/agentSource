from __future__ import annotations

import asyncio
import shutil
from dataclasses import asdict
from pathlib import Path

import pytest

from drugpaper.chem.splicer.adapter import substitute
from drugpaper.skills.protocol import ToolCaller
from drugpaper.skills.registry import BUILTIN_ROOT, SkillRegistry
from drugpaper.skills.routing import SkillRouting
from drugpaper.skills.runtime import SkillRuntime
from drugpaper.skills.watcher import SkillWatcher
from tests.unit.skills.conftest import FIXTURES


async def test_builtin_packages_pass_replay_and_python_tool_cases(env, tmp_path):
    db, _, runtime, ctx = env

    async def markush(**kw):
        return asdict(substitute(**kw))

    registry = SkillRegistry(
        db,
        snapshot_root=tmp_path / "builtin_snap",
        known_tools={
            "markush.substitute",
            "molecule.detect",
            "molparser.recognize",
            "chem.name_to_structure",
            "mol.deliver",
            "mol.lookup_references",
            "grep",
            "search",
        },
        known_tasks={
            "visual_classify",
            "paper_qualify",
            "figure_describe",
            "figure_structure",
            "figure_self_check",
            "figure_remediate_vision",
            "remediate_decide",
            "metric_map_fallback",
            "figure_query",
            "visual_reclassify",
            "text_extract",
        },
        tool_impls={"markush.substitute": markush},
    )
    await registry.scan([(BUILTIN_ROOT, "builtin")])
    rows = await registry.versions()
    assert len(rows) == len([p for p in BUILTIN_ROOT.iterdir() if (p / "manifest.yaml").exists()])
    assert all(r.test_report["ok"] for r in rows), [
        (r.name, r.errors, r.test_report) for r in rows if not r.test_report["ok"]
    ]
    assert {r.name for r in rows if r.status == "active"} == {
        p.name
        for p in BUILTIN_ROOT.iterdir()
        if (p / "manifest.yaml").exists() and p.name != "figure_describe"
    }
    ctx.tools = ToolCaller(frozenset(), {"markush.substitute": markush})
    outcome = await SkillRuntime(registry, db).run(
        "markush_assemble",
        ctx.target_ref,
        {"caption": "C1CCCCC1*<sep><a>6:R</a>", "definitions": {"R": "Me"}},
        ctx,
    )
    assert outcome.artifacts[0].payload["products"] == ["CC1CCCCC1"], outcome.error


async def test_hot_added_skill_and_redis_notification(env, tmp_path):
    _, registry, _, _ = env
    root = tmp_path / "system"
    root.mkdir()

    class Redis:
        messages = []

        async def publish(self, channel, payload):
            self.messages.append((channel, payload))

    redis = Redis()
    watcher = SkillWatcher(registry, [(root, "system")], redis=redis)
    stop = asyncio.Event()
    task = asyncio.create_task(watcher.watch(stop))
    try:
        await asyncio.sleep(0.2)
        shutil.copytree(FIXTURES / "demo_python", root / "demo_python")
        async with asyncio.timeout(5):
            while True:
                try:
                    skill = await registry.resolve("demo_python")
                    if redis.messages:
                        break
                    await asyncio.sleep(0.05)
                except KeyError:
                    await asyncio.sleep(0.05)
        assert skill.scope == "system" and redis.messages[-1][0] == "skills:changed"
    finally:
        stop.set()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


async def test_inflight_python_keeps_old_version_after_hot_update(env, tmp_path):
    _, registry, runtime, ctx = env
    root = tmp_path / "demo_python"
    shutil.copytree(FIXTURES / "demo_python", root)
    code = 'from drugpaper.skills.protocol import SkillResult,ArtifactDraft\nimport asyncio\nasync def run(inputs,ctx):\n await asyncio.sleep(.2)\n return SkillResult(artifacts=[ArtifactDraft(artifact_type="word_count",payload={"words":1})])\n'
    (root / "scripts/main.py").write_text(code)
    (root / "tests/cases/three_words.json").write_text(
        '{"inputs":{"text":"x"},"expect":{"payload":{"words":1}}}'
    )
    await registry.register(root)
    old = asyncio.create_task(runtime.run("demo_python", ctx.target_ref, {"text": "x"}, ctx, force=True))
    await asyncio.sleep(0.1)
    (root / "scripts/main.py").write_text(code.replace('"words":1', '"words":2'))
    (root / "tests/cases/three_words.json").write_text(
        '{"inputs":{"text":"x"},"expect":{"payload":{"words":2}}}'
    )
    await registry.register(root)
    first = await old
    second = await runtime.run("demo_python", ctx.target_ref, {"text": "x"}, ctx, force=True)
    assert first.artifacts[0].payload == {"words": 1} and second.artifacts[0].payload == {"words": 2}


async def test_routing_templates_merges_and_d38(env):
    _, registry, _, _ = env
    from drugpaper.skills.loader import load_package

    skills = {p.name: load_package(p) for p in BUILTIN_ROOT.iterdir() if (p / "manifest.yaml").exists()}
    routing = SkillRouting.from_file(Path("config/routing.yaml"), skills)
    assert "molecular_docking_extract" in routing.resolve_chain("molecular_docking")
    assert routing.resolve_chain("property_plot").count("chart_digitize") == 1
    assert "property_plot_extract" not in routing.resolve_chain("property_plot")
    assert routing.resolve_chain("data_chart") == [
        "molecule_detect",
        "molecule_recognize",
        "som_render",
        "chart_digitize",
        "mol_backfill",
    ]
    for s in skills.values():
        s.manifest.vision_structuring = True
    with pytest.raises(ValueError, match="D38"):
        SkillRouting(
            {
                "schema_version": 1,
                "routes": [{"match": {"subtype": "x"}, "run": ["visual_classify", "paper_qualify"]}],
            },
            skills,
        )
    SkillRouting(
        {
            "schema_version": 1,
            "routes": [{"match": {"subtype": "x"}, "run": ["visual_classify+paper_qualify"]}],
        },
        skills,
    )


async def test_redis_subscriber_refreshes_another_worker(env, tmp_path):
    db, registry, _, _ = env

    class PubSub:
        def __init__(self, queue):
            self.queue = queue

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def subscribe(self, channel):
            pass

        async def get_message(self, **kwargs):
            try:
                return await asyncio.wait_for(self.queue.get(), timeout=0.1)
            except TimeoutError:
                return None

    class Redis:
        def __init__(self):
            self.queue = asyncio.Queue()

        async def publish(self, channel, payload):
            await self.queue.put({"data": payload})

        def pubsub(self):
            return PubSub(self.queue)

    redis = Redis()
    worker = SkillRegistry(db, snapshot_root=tmp_path / "worker_snapshots")
    refreshed = asyncio.Event()

    async def refresh():
        refreshed.set()

    watcher = SkillWatcher(worker, [], redis=redis, on_change=refresh)
    stop = asyncio.Event()
    task = asyncio.create_task(watcher.subscribe(stop))
    try:
        await registry.register(FIXTURES / "demo_python")
        await redis.publish("skills:changed", "changed")
        await asyncio.wait_for(refreshed.wait(), timeout=2)
        assert worker.version == 1 and (await worker.resolve("demo_python")).name == "demo_python"
    finally:
        stop.set()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


async def test_removed_package_disables_capability(env, tmp_path):
    _, registry, _, _ = env
    root = tmp_path / "system"
    root.mkdir()
    shutil.copytree(FIXTURES / "demo_python", root / "demo_python")
    watcher = SkillWatcher(registry, [(root, "system")])
    await watcher.reload()
    assert (await registry.resolve("demo_python")).name == "demo_python"
    shutil.rmtree(root / "demo_python")
    await watcher.reload()
    with pytest.raises(KeyError):
        await registry.resolve("demo_python")
