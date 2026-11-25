from __future__ import annotations

import shutil

import pytest
import yaml

from tests.unit.skills.conftest import FIXTURES


async def test_registry_lifecycle_versions_and_rollback(env, tmp_path):
    _, registry, _, ctx = env
    root = tmp_path / "demo_python"
    shutil.copytree(FIXTURES / "demo_python", root)
    first = await registry.register(root)
    assert first.status == "active"
    pinned = await registry.resolve("demo_python")
    (root / "scripts/main.py").write_text(
        (root / "scripts/main.py").read_text().replace("words = len", "words = 1 + len")
    )
    (root / "tests/cases/three_words.json").write_text(
        ' {"inputs":{"text":"a b c"},"expect":{"payload":{"words":4}}} '
    )
    second = await registry.register(root)
    assert second.id != first.id
    assert (await registry.get_version(first.id)).status == "staged"
    assert (await registry.resolve("demo_python")).content_hash != pinned.content_hash
    assert "words = len" in (pinned.path / "scripts/main.py").read_text()
    await registry.activate(first.id)
    assert (await registry.resolve("demo_python")).content_hash == pinned.content_hash
    await registry.disable(first.id)
    with pytest.raises(KeyError):
        await registry.resolve("demo_python")


async def test_invalid_packages_remain_queryable(env, tmp_path):
    _, registry, _, _ = env
    root = tmp_path / "broken"
    root.mkdir()
    (root / "manifest.yaml").write_text("name: broken")
    row = await registry.register(root)
    assert row.status == "invalid" and row.errors[0]["code"] == "E_MANIFEST_SCHEMA"
    with pytest.raises(ValueError):
        await registry.activate(row.id)


async def test_scopes_and_audience(env, tmp_path):
    _, registry, _, _ = env
    await registry.register(FIXTURES / "demo_python")
    root = tmp_path / "demo_python"
    shutil.copytree(FIXTURES / "demo_python", root)
    with pytest.raises(ValueError, match="override"):
        await registry.register(root, scope="system")
    m = yaml.safe_load((root / "manifest.yaml").read_text())
    m["override"] = True
    (root / "manifest.yaml").write_text(yaml.safe_dump(m))
    await registry.register(root, scope="system")
    assert (await registry.resolve("demo_python")).scope == "system"
    assert await registry.list_visible(audience="figure_agent") == []
    with pytest.raises(ValueError, match="user"):
        await registry.register(root, scope="user")


async def test_case_failure_prevents_activation(env, tmp_path):
    _, registry, _, _ = env
    root = tmp_path / "demo_prompt"
    shutil.copytree(FIXTURES / "demo_prompt", root)
    (root / "tests/cases/basic.response.json").write_text('{"label":5}')
    row = await registry.register(root)
    assert row.status == "invalid" and row.test_report["ok"] is False


async def test_snapshot_survives_worker_restart_and_common_rule_update(env, tmp_path):
    _, registry, _, _ = env
    await registry.register(FIXTURES / "demo_prompt")
    pinned = await registry.resolve("demo_prompt")
    common = pinned.path.parent / "_common/common/data_boundary.md"
    assert common.exists()
    from drugpaper.skills.registry import SkillRegistry

    restarted = SkillRegistry(registry.db, snapshot_root=tmp_path / "other")
    again = await restarted.resolve("demo_prompt")
    assert again.fragments == pinned.fragments and again.content_hash == pinned.content_hash
