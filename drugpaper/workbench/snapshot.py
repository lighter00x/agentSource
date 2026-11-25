from __future__ import annotations

import ast
import asyncio
import shutil
from pathlib import Path
from typing import Any

from drugpaper.skills.loader import load_package
from drugpaper.workbench.store import ROOT, digest, editable, safe_path


def files() -> list[str]:
    return sorted(
        str(p.relative_to(ROOT))
        for folder in [
            "drugpaper/skills",
            "drugpaper/tools",
            "drugpaper/chem",
            "drugpaper/vision",
            "prompts",
            "config",
        ]
        for p in (ROOT / folder).rglob("*")
        if p.is_file() and editable(str(p.relative_to(ROOT))) and "__pycache__" not in p.parts
    )


async def create(sv: Any, workspace: Path, overrides: dict[str, str]) -> dict[str, str]:
    bundle = await sv.skills()
    skills = await bundle.registry.list_visible()
    return await asyncio.to_thread(
        _create, workspace, overrides, skills, sv.settings.models_config_path, sv.settings.routing_config_path
    )


def _create(
    workspace: Path, overrides: dict[str, str], skills: list[Any], models: Path, routing: Path
) -> dict[str, str]:
    workspace.mkdir(parents=True)
    for folder in ["drugpaper", "prompts", "config"]:
        shutil.copytree(
            ROOT / folder, workspace / folder, ignore=shutil.ignore_patterns("__pycache__", "*.pyc")
        )
    shutil.copyfile(models, workspace / "config/models.yaml")
    shutil.copyfile(routing, workspace / "config/routing.yaml")
    for skill in skills:
        target = workspace / "drugpaper/skills/builtin" / skill.name
        if target.exists():
            shutil.rmtree(target)
        shutil.copytree(skill.path, target, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        for name, text in skill.fragments.items():
            p = workspace / "prompts" / name
            if not name.endswith(".md"):
                p = p.with_suffix(".md")
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(text)
    for name, text in overrides.items():
        if not editable(name):
            raise ValueError("不可编辑此路径")
        p = safe_path(workspace, name)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
    return {
        str(p.relative_to(workspace)): digest(p.read_text())
        for p in workspace.rglob("*")
        if p.is_file() and editable(str(p.relative_to(workspace)))
    }


def validate(workspace: Path) -> list[dict[str, Any]]:
    result = []
    skills = {}
    for p in workspace.rglob("*.py"):
        ast.parse(p.read_text(), filename=str(p))
    for p in (workspace / "drugpaper/skills/builtin").glob("*/manifest.yaml"):
        skill = load_package(p.parent, prompts_root=workspace / "prompts")
        skills[skill.name] = skill
        if not skill.ok:
            result.append({"skill": p.parent.name, "errors": [str(e) for e in skill.errors]})
    if not result:
        from drugpaper.skills.routing import SkillRouting

        SkillRouting.from_file(workspace / "config/routing.yaml", skills)
    return result
