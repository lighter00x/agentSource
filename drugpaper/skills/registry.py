"""数据库版本注册表；注册时冻结包文件，正在运行的版本不会被热更新修改。"""

from __future__ import annotations

import asyncio
import json
import shutil
from dataclasses import asdict
from pathlib import Path
from typing import Any, cast

from jsonschema import Draft202012Validator
from sqlalchemy import select

from drugpaper.domain.ids import new_id
from drugpaper.gateway.base import ChatRequest, ChatResponse
from drugpaper.persistence.db import Database
from drugpaper.persistence.models import SkillVersionRow, utcnow
from drugpaper.skills.execution import execute
from drugpaper.skills.loader import LoadedSkill, case_files, load_package
from drugpaper.skills.materials import Materials
from drugpaper.skills.protocol import Scope, SkillBudget, SkillContext, ToolCaller, ToolFn

BUILTIN_ROOT = Path(__file__).parent / "builtin"


class ReplayModel:
    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload

    async def chat(self, req: ChatRequest, *, task: str = "default") -> ChatResponse:
        return ChatResponse(content=json.dumps(self.payload))


class SkillRegistry:
    def __init__(
        self,
        db: Database,
        *,
        snapshot_root: Path,
        known_tools: set[str] | None = None,
        known_tasks: set[str] | None = None,
        tool_impls: dict[str, ToolFn] | None = None,
    ) -> None:
        self.db = db
        self.snapshot_root = snapshot_root
        self.tool_impls = tool_impls or {}
        self.known_tools = known_tools
        self.known_tasks = known_tasks
        self._packages: dict[str, LoadedSkill] = {}
        self.local_packages: dict[str, LoadedSkill] = {}
        self.version = 0

    async def register(
        self, path: Path, *, scope: Scope = "builtin", activate: bool | None = None
    ) -> SkillVersionRow:
        if scope == "user":
            raise ValueError("P3 尚未启用 user 作用域")
        skill = load_package(path, scope=scope, known_tools=self.known_tools, known_tasks=self.known_tasks)
        snapshot = self.snapshot_root / skill.content_hash / path.name
        if skill.ok:
            snapshot.parent.mkdir(parents=True, exist_ok=True)
            if not snapshot.exists():
                shutil.copytree(path, snapshot, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
            shared = snapshot.parent / "_common"
            for name, text in skill.fragments.items():
                file = (shared / name).with_suffix(".md") if not name.endswith(".md") else shared / name
                file.parent.mkdir(parents=True, exist_ok=True)
                if not file.exists():
                    file.write_text(text)
            # 从同一份快照重新加载，避免后续入口读取正在变化的源目录。
            frozen = load_package(
                snapshot,
                scope=scope,
                known_tools=self.known_tools,
                known_tasks=self.known_tasks,
                prompts_root=shared,
            )
            if frozen.content_hash != skill.content_hash:
                raise ValueError("Skill 文件在注册过程中变化，请重试")
            skill = frozen
        async with self.db.session() as s:
            row = (
                (
                    await s.execute(
                        select(SkillVersionRow).where(
                            SkillVersionRow.name == skill.name,
                            SkillVersionRow.content_hash == skill.content_hash,
                            SkillVersionRow.scope == scope,
                            SkillVersionRow.owner_user_id == "",
                        )
                    )
                )
                .scalars()
                .first()
            )
            if row is not None:
                self._packages[row.id] = skill
                return row
            row = SkillVersionRow(
                id=new_id("sk"),
                name=skill.name,
                version=skill.version,
                content_hash=skill.content_hash,
                scope=scope,
                status="discovered",
                path=str(snapshot if skill.ok else path),
                manifest=skill.manifest.model_dump() if skill.manifest else {},
                errors=[asdict(e) for e in skill.errors],
                warnings=[asdict(w) for w in skill.warnings],
            )
            s.add(row)
            await s.flush()
            row.status = "validating"
            if not skill.ok:
                row.status = "invalid"
            else:
                row.status = "testing"
                report = await self.test_package(skill)
                row.test_report = report
                row.status = "staged" if report["ok"] else "invalid"
                if not report["ok"]:
                    row.errors = [{"code": "E_CASE_FAILED", "message": report["cases"]}]
            self._packages[row.id] = skill
        self.version += 1
        manifest = skill.manifest
        if (
            row.status == "staged"
            and manifest
            and manifest.enabled
            and (activate if activate is not None else manifest.auto_activate)
        ):
            await self.activate(row.id)
        return await self.get_version(row.id)

    async def test_package(self, skill: LoadedSkill) -> dict[str, Any]:
        from drugpaper.skills.runtime import BudgetedModel, validate_result

        report: list[dict[str, Any]] = []
        for path in case_files(skill.path):
            try:
                case = json.loads(path.read_text())
                response_file = case.get("response_file")
                payload = json.loads((path.parent / response_file).read_text()) if response_file else {}
                manifest = skill.manifest
                assert manifest is not None
                ctx = SkillContext(
                    "default",
                    "doc_case",
                    1,
                    case.get("target", {}).get("ref", "doc_case"),
                    new_id("run"),
                    new_id("tr"),
                    ReplayModel(payload),
                    ToolCaller(frozenset(manifest.tools), self.tool_impls),
                    None,
                    SkillBudget(manifest.budgets.max_model_calls, manifest.budgets.max_wall_time_s),
                    materials=Materials(
                        target=case.get("target", {}),
                        images=[
                            __import__(
                                "drugpaper.gateway.base", fromlist=["ImagePart"]
                            ).ImagePart.model_validate(image)
                            for image in case.get("images", [])
                        ],
                    ),
                )
                ctx.model = BudgetedModel(ctx.model, skill, ctx.budget)
                async with asyncio.timeout(manifest.budgets.max_wall_time_s):
                    result = await execute(skill, case["inputs"], ctx)
                validate_result(skill, ctx.target_ref, result, ctx)
                expected = case.get("expect", {})
                for key, value in expected.get("payload", {}).items():
                    if result.artifacts[0].payload.get(key) != value:
                        raise ValueError(f"期望字段 {key}={value!r}")
                if "status" in expected and result.artifacts[0].status != expected["status"]:
                    raise ValueError("结果状态不符")
                Draft202012Validator(skill.input_schema).validate(case["inputs"])
                report.append({"name": path.stem, "ok": True})
            except Exception as exc:
                report.append({"name": path.stem, "ok": False, "error": str(exc)})
        return {"ok": all(c["ok"] for c in report), "cases": report}

    async def get_version(self, version_id: str) -> SkillVersionRow:
        async with self.db.session() as s:
            row = await s.get(SkillVersionRow, version_id)
            if row is None:
                raise KeyError(version_id)
            return row

    async def activate(self, version_id: str) -> None:
        async with self.db.session() as s:
            row = await s.get(SkillVersionRow, version_id)
            if row is None or row.status not in {"staged", "active", "disabled"}:
                raise ValueError("只有通过验证的版本可以激活")
            if self.db.is_postgres:
                from sqlalchemy import text

                await s.execute(
                    text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
                    {"key": "skill:" + row.name + ":" + row.scope},
                )
            visible = list(
                (
                    await s.execute(
                        select(SkillVersionRow).where(
                            SkillVersionRow.name == row.name, SkillVersionRow.status == "active"
                        )
                    )
                ).scalars()
            )
            if (
                any(v.scope == "builtin" for v in visible)
                and row.scope == "system"
                and not row.manifest.get("override", False)
            ):
                raise ValueError("跨作用域覆盖需要 override: true")
            for previous in visible:
                if previous.scope == row.scope:
                    previous.status = "staged"
            await s.flush()
            row.status = "active"
            row.activated_at = utcnow()
        self.version += 1

    async def disable(self, version_id: str) -> None:
        async with self.db.session() as s:
            row = await s.get(SkillVersionRow, version_id)
            if row is None:
                raise KeyError(version_id)
            row.status = "disabled"
        self.version += 1

    async def versions(self, name: str | None = None) -> list[SkillVersionRow]:
        async with self.db.session() as s:
            stmt = select(SkillVersionRow).order_by(SkillVersionRow.created_at, SkillVersionRow.id)
            if name:
                stmt = stmt.where(SkillVersionRow.name == name)
            return list((await s.execute(stmt)).scalars())

    async def resolve(self, name: str, user_id: str = "default") -> LoadedSkill:
        if name in self.local_packages:
            return self.local_packages[name]
        rows = [
            r for r in await self.versions(name) if r.status == "active" and r.scope in {"builtin", "system"}
        ]
        rows.sort(key=lambda r: r.scope == "system", reverse=True)
        if not rows:
            raise KeyError(f"没有 active Skill：{name}")
        row = rows[0]
        if row.id not in self._packages:
            self._packages[row.id] = load_package(
                Path(row.path or ""),
                scope=cast(Scope, row.scope),
                prompts_root=Path(row.path or "").parent / "_common",
            )
        return self._packages[row.id]

    async def list_visible(self, user_id: str = "default", audience: str | None = None) -> list[LoadedSkill]:
        names = sorted(
            {r.name for r in await self.versions() if r.status == "active"} | set(self.local_packages)
        )
        skills = [await self.resolve(n, user_id) for n in names]
        return [
            s for s in skills if s.manifest and (not audience or audience in s.manifest.disclosure.audiences)
        ]

    async def scan(self, roots: list[tuple[Path, Scope]]) -> None:
        for root, scope in roots:
            if root.exists():
                for package in sorted(root.glob("*/manifest.yaml")):
                    await self.register(package.parent, scope=scope)
                for row in await self.versions():
                    if (
                        row.scope == scope
                        and row.status == "active"
                        and not (root / row.name / "manifest.yaml").exists()
                    ):
                        await self.disable(row.id)
