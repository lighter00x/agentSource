"""Skill 执行、预算、缓存和原子提交；失败不留下半份 Artifact。"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field, replace
from typing import Any

from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError as JsonSchemaError
from sqlalchemy.exc import IntegrityError

from drugpaper.domain.artifacts import Artifact
from drugpaper.domain.ids import new_id
from drugpaper.domain.work_units import node_dependency, work_unit_key
from drugpaper.facts.metric_dictionary import MetricDictionary
from drugpaper.gateway.base import ChatRequest, ChatResponse, ModelClient
from drugpaper.persistence.db import Database
from drugpaper.persistence.models import RunRow, SkillProposalRow, utcnow
from drugpaper.persistence.repos import artifacts as repo
from drugpaper.skills.execution import execute
from drugpaper.skills.loader import LoadedSkill
from drugpaper.skills.protocol import SkillBudget, SkillContext, SkillResult
from drugpaper.skills.registry import SkillRegistry


@dataclass
class SkillOutcome:
    status: str
    run_id: str
    artifacts: list[Artifact] = field(default_factory=list)
    error: str | None = None
    cached: bool = False


class BudgetedModel:
    def __init__(self, model: ModelClient, skill: LoadedSkill, budget: SkillBudget) -> None:
        self.model, self.skill, self.budget = model, skill, budget

    async def chat(self, req: ChatRequest, *, task: str = "default") -> ChatResponse:
        manifest = self.skill.manifest
        assert manifest is not None
        if task not in manifest.model_tasks.values():
            raise PermissionError(f"Skill 未声明模型任务：{task}")
        if self.budget.model_calls >= self.budget.max_model_calls:
            raise ValueError("max_model_calls budget exhausted")
        self.budget.model_calls += 1
        if manifest.budgets.max_tokens:
            req = req.model_copy(update={"max_tokens": min(req.max_tokens, manifest.budgets.max_tokens)})
        return await self.model.chat(req, task=task)


def input_fingerprint(skill: LoadedSkill, inputs: dict[str, Any], ctx: SkillContext) -> str:
    router = getattr(ctx.model, "router", None)
    manifest = skill.manifest
    specs = (
        {task: router.public_config(task) for task in manifest.model_tasks.values()}
        if router and manifest
        else ctx.extras.get("model_spec", {})
    )
    upstreams = ctx.materials.upstream_versions if ctx.materials else {}
    target = ctx.materials.target if ctx.materials else {}
    if skill.name in {"text_extract", "name_to_structure"}:
        # 正文抽取不依赖自己的历史产物，也不依赖后来更新的化学台账。
        target = {k: target.get(k) for k in ("ref", "type", "text_raw", "section_role")}
        upstreams = {}
    if skill.name in {"molecule_recognize", "som_render"}:
        # 派生的 structure_id/自身 Artifact 不应反过来改变同一份裁剪材料的缓存键。
        target = {key: target[key] for key in ("ref", "type", "regions", "geometry") if key in target}
        if skill.name == "molecule_recognize":
            attrs = ctx.materials.target.get("attributes", {}) if ctx.materials else {}
            target["masks"] = attrs.get("masked_regions", [])
            target["children"] = (
                [
                    {
                        "geometry": n["geometry"],
                        "masked_regions": n["attributes"].get("masked_regions", []),
                        "status": n["status"],
                    }
                    for n in ctx.materials.target.get("children", [])
                ]
                if ctx.materials
                else []
            )
        else:
            target["boxes"] = (
                [
                    {
                        "geometry": n["geometry"],
                        "reading_index": n["attributes"]["reading_index"],
                        "status": n["status"],
                    }
                    for n in ctx.materials.target.get("children", [])
                ]
                if ctx.materials
                else []
            )
        upstreams = {}
    if manifest and (
        manifest.vision_structuring
        or skill.name in {"visual_classify", "figure_self_check", "visual_reclassify"}
    ):
        target = {
            k: target[k]
            for k in ("ref", "type", "geometry", "regions", "caption", "blackboard")
            if k in target
        }
        if manifest.vision_structuring and ctx.materials:
            target["structures"] = [
                {k: s.get(k) for k in ("node_ref", "confidence", "validation")}
                for s in ctx.materials.target.get("structures", [])
            ]
            target["children"] = [
                {k: n.get(k) for k in ("ref", "geometry", "status")}
                for n in ctx.materials.target.get("children", [])
            ]
        upstreams = {}
    record = {
        "execution_contract": "skill-delivery-v2",
        "framework_version": __import__("drugpaper").__version__,
        "inputs": inputs,
        "target": ctx.target_ref,
        "skill": skill.content_hash,
        "prompt": skill.prompt_hash,
        "model": specs,
        "user_id": ctx.user_id,
        "tool_fingerprints": ctx.extras.get("tool_fingerprints", {}),
        "debug_code": ctx.extras.get("debug_code", ""),
        "upstreams": upstreams,
        "node_hash": ""
        if skill.name
        in {
            "molecule_recognize",
            "som_render",
            "visual_classify",
            "figure_self_check",
            "visual_reclassify",
            "text_extract",
            "name_to_structure",
        }
        or manifest
        and manifest.vision_structuring
        else ctx.materials.node_hash
        if ctx.materials
        else "",
        "target_material": target,
        "metric_dictionary": MetricDictionary().metrics
        if skill.name in {"measurement_map", "sar_record", "chart_measurements"}
        else None,
        "reference_images": [i.model_dump() for i in ctx.extras.get("reference_images", [])],
        "images": [i.model_dump() for i in ctx.materials.images] if ctx.materials else [],
    }
    return hashlib.sha256(
        json.dumps(record, sort_keys=True, ensure_ascii=False, default=str).encode()
    ).hexdigest()


def validate_result(skill: LoadedSkill, target_ref: str, result: SkillResult, ctx: SkillContext) -> None:
    manifest = skill.manifest
    assert manifest is not None
    if not result.artifacts or len(result.artifacts) != 1:
        raise ValueError("P3 每个 Work Unit 必须提交一个 Artifact")
    if (
        result.quality.status == "failed" or any(draft.status == "failed" for draft in result.artifacts)
    ) and (result.fact_proposals or result.node_proposals or result.relation_proposals):
        raise ValueError("质量失败的产物不得提交事实、节点或关系提案")
    for draft in result.artifacts:
        Draft202012Validator(skill.output_schema).validate(draft.payload)
        if draft.artifact_type != manifest.produces.artifact_type or draft.target_ref not in {
            None,
            target_ref,
        }:
            raise PermissionError("Artifact 类型或目标越权")
    for fact in result.fact_proposals:
        if fact.fact_type not in manifest.produces.fact_types:
            raise PermissionError(f"fact type not allowed：{fact.fact_type}")
    target_local = target_ref.rsplit("/", 1)[-1]
    regions = ctx.materials.target.get("regions", []) if ctx.materials else []
    for node in result.node_proposals:
        if (
            node.node_type not in manifest.produces.node_types
            or node.parent_local_id != target_local
            or not node.local_id.startswith(target_local + ".")
        ):
            raise PermissionError("子节点类型或父节点越权")
        x0, y0, x1, y1 = node.local_bbox
        if not (0 <= x0 < x1 <= 1 and 0 <= y0 < y1 <= 1):
            raise ValueError("子节点几何越界")
        if node.parent_region_index < 0 or node.parent_region_index >= len(regions):
            raise ValueError("子节点 parent_region_index 无效")
    for relation in result.relation_proposals:
        allowed = {target_ref, target_local} | {n.local_id for n in result.node_proposals}
        if relation.source not in allowed or relation.target not in allowed:
            raise PermissionError("关系端点越权")


Validator = Callable[[LoadedSkill, SkillResult, SkillContext], Awaitable[None]]
LockCheck = Callable[[str, SkillResult, SkillContext], Awaitable[bool]]
EventSink = Callable[[str, dict[str, Any]], Awaitable[None]]


class SkillRuntime:
    def __init__(
        self,
        registry: SkillRegistry,
        db: Database,
        *,
        validators: dict[str, Validator] | None = None,
        lock_check: LockCheck | None = None,
        event_sink: EventSink | None = None,
    ) -> None:
        self.registry, self.db = registry, db
        self.validators = validators or {}
        self.lock_check, self.event_sink = lock_check, event_sink
        self._locks: dict[str, asyncio.Lock] = {}

    async def run(
        self,
        skill_name: str,
        target_ref: str,
        inputs: dict[str, Any],
        ctx: SkillContext,
        *,
        force: bool = False,
    ) -> SkillOutcome:
        skill = await self.registry.resolve(skill_name, ctx.user_id)
        manifest = skill.manifest
        assert manifest is not None
        run_id = new_id("run")
        budget = SkillBudget(
            min(
                manifest.budgets.max_model_calls,
                ctx.extras.get("max_model_calls", manifest.budgets.max_model_calls),
            ),
            min(
                manifest.budgets.max_wall_time_s,
                ctx.extras.get("max_wall_time_s", manifest.budgets.max_wall_time_s),
            ),
        )
        current = replace(ctx, target_ref=target_ref, run_id=run_id, skill_id=skill.skill_id, budget=budget)
        fp = input_fingerprint(skill, inputs, current)
        current.model = BudgetedModel(ctx.model, skill, budget)
        current.tools = replace(ctx.tools, allowed=frozenset(manifest.tools))
        key = work_unit_key(skill_name, target_ref)
        # 同进程去重；跨进程 PostgreSQL 在提交中使用事务级 advisory lock。
        async with self._locks.setdefault(key, asyncio.Lock()):
            # 检测 Tool 自己按权重哈希缓存；没有后端版本时不能在调用前复用 Skill Artifact。
            detection_versioned = "molecule.detect" not in manifest.tools or bool(
                ctx.extras.get("tool_fingerprints", {}).get("molecule.detect")
            )
            if manifest.cache.enabled and detection_versioned and not force:
                async with self.db.session(ctx.user_id) as s:
                    hit = await repo.find_by_fingerprint(s, key, fp)
                    # Service outages and unsupported names must remain retryable.
                    if (
                        hit
                        and skill.name == "name_to_structure"
                        and (hit.payload or {}).get("status") == "needs_review"
                    ):
                        hit = None
                    if hit:
                        s.add(
                            RunRow(
                                id=run_id,
                                user_id=ctx.user_id,
                                document_id=ctx.document_id,
                                run_type="skill",
                                component=skill.name,
                                component_version=skill.version,
                                component_hash=skill.content_hash,
                                inputs=inputs,
                                outputs={"artifact_ids": [hit.id], "cached": True},
                                trace_id=ctx.trace_id,
                                parent_run_id=ctx.run_id,
                                status="completed",
                                ended_at=utcnow(),
                                metrics={"model_calls": 0},
                            )
                        )
                        return SkillOutcome("completed", run_id, [repo.to_domain(hit)], cached=True)
            try:
                async with asyncio.timeout(budget.max_wall_time_s):
                    Draft202012Validator(skill.input_schema).validate(inputs)
                    target_type = current.materials.target.get("type") if current.materials else None
                    if manifest.accepts.node_types and target_type not in manifest.accepts.node_types:
                        raise ValueError("Skill 不接受该节点类型")
                    result = await execute(skill, inputs, current)
                    for artifact in result.artifacts:
                        payload = artifact.payload
                        if result.quality.status == "failed":
                            artifact.status = "failed"
                        elif result.quality.status == "needs_review" and artifact.status == "completed":
                            artifact.status = "needs_review"
                        if artifact.status == "completed" and (
                            payload.get("status") in {"unclear", "not_a_table", "failed", "needs_review"}
                            or payload.get("needs_review")
                            or payload.get("errors")
                            or payload.get("semantic_errors")
                        ):
                            artifact.status = "needs_review"
                    validate_result(skill, target_ref, result, current)
                    if self.lock_check and await self.lock_check(target_ref, result, current):
                        raise PermissionError("目标已人工锁定，结果需解决冲突")
                    for name in manifest.validators:
                        if name != "schema":
                            validator = self.validators.get(name)
                            if validator is None:
                                raise ValueError(f"未知校验器：{name}")
                            await validator(skill, result, current)
                    artifacts = await self._commit(skill, key, fp, inputs, result, current)
            except Exception as exc:
                message = (
                    (
                        "输入/产物Schema校验失败，字段 "
                        + ".".join(map(str, exc.absolute_path))
                        + ": "
                        + (
                            "is not of type " + str(exc.validator_value)
                            if exc.validator == "type"
                            else exc.message[:400]
                        )
                    )
                    if isinstance(exc, JsonSchemaError)
                    else str(exc)
                )
                async with self.db.session(ctx.user_id) as s:
                    s.add(
                        RunRow(
                            id=run_id,
                            user_id=ctx.user_id,
                            document_id=ctx.document_id,
                            run_type="skill",
                            component=skill.name,
                            component_version=skill.version,
                            component_hash=skill.content_hash,
                            inputs=inputs,
                            outputs={},
                            trace_id=ctx.trace_id,
                            parent_run_id=ctx.run_id,
                            status="failed",
                            error={"type": type(exc).__name__, "message": message},
                            metrics={"model_calls": budget.model_calls},
                            ended_at=utcnow(),
                        )
                    )
                return SkillOutcome("failed", run_id, error=f"{type(exc).__name__}: {message}")
            if self.event_sink:
                try:
                    await self.event_sink(
                        "artifact.updated",
                        {"run_id": run_id, "artifact_ids": [a.artifact_id for a in artifacts]},
                    )
                except Exception:
                    # 提交已经成功；通知失败不能把可交付产物报告为执行失败。
                    logging.getLogger(__name__).exception("Artifact notification failed for run %s", run_id)
            return SkillOutcome("completed", run_id, artifacts)

    async def _commit(
        self,
        skill: LoadedSkill,
        key: str,
        fp: str,
        inputs: dict[str, Any],
        result: SkillResult,
        ctx: SkillContext,
    ) -> list[Artifact]:
        for attempt in range(3):
            try:
                async with self.db.session(ctx.user_id) as s:
                    if self.db.is_postgres:
                        from sqlalchemy import text

                        await s.execute(
                            text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
                            {"key": "document:" + (ctx.document_id or key)},
                        )
                    draft = result.artifacts[0]
                    row = await repo.create_version(
                        s,
                        document_id=ctx.document_id or ctx.target_ref,
                        artifact_type=draft.artifact_type,
                        target_ref=ctx.target_ref,
                        work_unit_key=key,
                        payload=draft.payload,
                        input_fingerprint=fp,
                        run_id=ctx.run_id,
                        status=draft.status,
                        confidence=draft.confidence,
                        schema_name=draft.schema_name,
                        schema_version=draft.schema_version,
                        user_id=ctx.user_id,
                    )
                    upstreams = list(draft.upstreams)
                    if ctx.materials:
                        upstreams.extend(ctx.materials.upstream_versions)
                        if ctx.materials.node_hash:
                            upstreams.append(node_dependency(ctx.target_ref, ctx.materials.node_hash))
                    await repo.add_dependencies(s, row.id, sorted(set(upstreams)), user_id=ctx.user_id)
                    from drugpaper.facts.projector import project

                    await project(s, row.id, result, ctx, skill.name)
                    from drugpaper.facts.figure_projector import project as project_figure

                    await project_figure(s, row.id, result, ctx)
                    if skill.name in {"text_extract", "name_to_structure"}:
                        from drugpaper.facts.projector import owned_node
                        from drugpaper.persistence.models import Document

                        document = await s.get(Document, ctx.document_id)
                        node = await owned_node(s, ctx, ctx.target_ref)
                        if (
                            document is None
                            or document.active_revision != ctx.revision
                            or node.status == "removed"
                            or ctx.materials is None
                            or node.text_raw != ctx.materials.target.get("text_raw")
                            or node.section_role != ctx.materials.target.get("section_role")
                        ):
                            raise ValueError("正文或revision已经改变，请重新抽取")
                    for kind, proposals in (
                        ("node", result.node_proposals),
                        ("fact", result.fact_proposals),
                        ("relation", result.relation_proposals),
                    ):
                        for proposal in proposals:
                            s.add(
                                SkillProposalRow(
                                    id=new_id("prop"),
                                    user_id=ctx.user_id,
                                    document_id=ctx.document_id,
                                    artifact_id=row.id,
                                    run_id=ctx.run_id,
                                    kind=kind,
                                    target_ref=ctx.target_ref,
                                    payload=proposal.model_dump(mode="json"),
                                )
                            )
                    s.add(
                        RunRow(
                            id=ctx.run_id,
                            user_id=ctx.user_id,
                            document_id=ctx.document_id,
                            run_type="skill",
                            component=skill.name,
                            component_version=skill.version,
                            component_hash=skill.content_hash,
                            inputs=inputs,
                            outputs={
                                "artifact_ids": [row.id],
                                "prompt_hash": skill.prompt_hash,
                                "quality": result.quality.model_dump(),
                            },
                            model=ctx.extras.get("model_spec"),
                            parent_run_id=ctx.extras.get("parent_run_id"),
                            trace_id=ctx.trace_id,
                            status="completed",
                            ended_at=utcnow(),
                            metrics={"model_calls": ctx.budget.model_calls},
                        )
                    )
                    return [repo.to_domain(row)]
            except IntegrityError:
                if attempt == 2:
                    raise
        raise RuntimeError("Artifact 提交重试耗尽")
