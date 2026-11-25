"""共享装配：注册表、Runtime、工具索引和材料，供 API / CLI / 验证脚本注入。"""

from __future__ import annotations

import base64
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from drugpaper.capabilities.index import CapabilityIndex
from drugpaper.capabilities.toolset import SkillTool
from drugpaper.chem.splicer.adapter import substitute
from drugpaper.docsrc.ir_source import IrDocumentSource
from drugpaper.domain.ids import parse_ref
from drugpaper.gateway.base import ImagePart
from drugpaper.gateway.task_router import RoutedModelClient, TaskRouter
from drugpaper.persistence.models import Document
from drugpaper.skills.materials import Materials
from drugpaper.skills.protocol import SkillBudget, SkillContext, ToolCaller, ToolFn
from drugpaper.skills.registry import BUILTIN_ROOT, SkillRegistry
from drugpaper.skills.runtime import SkillRuntime
from drugpaper.skills.watcher import SkillWatcher
from drugpaper.tools.builtin.chem import make_chem_tools
from drugpaper.tools.builtin.compounds import make_compound_tools
from drugpaper.tools.builtin.figure_facts import make_figure_fact_tools
from drugpaper.tools.builtin.molecule_detection import make_detection_tools
from drugpaper.tools.builtin.molparser import make_molparser_tools
from drugpaper.tools.builtin.opsin import make_opsin_tools
from drugpaper.tools.protocol import ToolContext
from drugpaper.tools.registry import ToolRegistry

if TYPE_CHECKING:
    from drugpaper.services import Services


@dataclass
class SkillServices:
    registry: SkillRegistry
    runtime: SkillRuntime
    tools: ToolRegistry
    index: CapabilityIndex
    models: RoutedModelClient
    services: Services
    watcher: SkillWatcher

    async def refresh(self) -> None:
        for skill in await self.registry.list_visible():
            self.tools.replace(SkillTool(skill, self.runtime, self.context_for))
        await self.index.refresh()

    async def prepare_vision(self, ctx: SkillContext) -> None:
        """Prepare the same numbered/original pair for autonomous Skill calls."""
        from sqlalchemy import select

        from drugpaper.persistence.models import ArtifactRow
        from drugpaper.skills.runtime import input_fingerprint

        assert ctx.materials is not None
        async with self.services.db.session(ctx.user_id) as session:
            rows = list(
                (
                    await session.execute(
                        select(ArtifactRow)
                        .where(
                            ArtifactRow.user_id == ctx.user_id,
                            ArtifactRow.target_ref == ctx.target_ref,
                            ArtifactRow.is_active.is_(True),
                            ArtifactRow.artifact_type.in_(["som_image", "molecule_boxes"]),
                        )
                        .order_by(ArtifactRow.created_at.desc())
                    )
                ).scalars()
            )
        som = next((r for r in rows if r.artifact_type == "som_image"), None)
        detection = next((r for r in rows if r.artifact_type == "molecule_boxes"), None)
        if som is None or detection is None:
            raise ValueError(
                "先调用molecule_detect，再molecule_recognize和som_render；当前图像缺少检测/编号产物"
            )
        if som.status not in {"completed", "needs_review"} or detection.status not in {
            "completed",
            "needs_review",
        }:
            raise ValueError("检测/编号产物执行失败，请重新执行molecule_detect，再重新调用som_render")
        som_skill = await self.registry.resolve("som_render")
        if som.input_fingerprint != input_fingerprint(som_skill, {}, ctx):
            raise ValueError("编号图与当前分子框不一致，请重新调用som_render")
        original = list(ctx.materials.images)
        numbered = [
            ImagePart(
                data_b64=base64.b64encode(
                    await self.services.store_for(ctx.user_id).get(item["blob_ref"])
                ).decode(),
                mime="image/png",
            )
            for item in (som.payload or {}).get("images", [])
        ]
        if len(numbered) != len(original) or not numbered:
            raise ValueError("编号图与原图分段不一致，请重新调用som_render")
        ctx.materials.images = numbered
        ctx.extras["reference_images"] = original

    async def context_for(
        self, ctx: ToolContext, target_ref: str, *, include_document_image: bool = True
    ) -> SkillContext:
        from dataclasses import asdict

        parsed = parse_ref(target_ref)
        document_id = parsed[0] if parsed else target_ref
        revision = parsed[1] if parsed else None
        if ctx.document_id and ctx.document_id != document_id:
            raise PermissionError("不能访问其他文档")
        async with self.services.db.session(ctx.user_id) as session:
            document = await session.get(Document, document_id)
            if document is None or document.user_id != ctx.user_id:
                raise PermissionError("目标文档不存在或不可见")
            if revision is not None and revision != document.active_revision:
                raise ValueError("目标revision已经过期，请使用当前文档节点")
        src = await IrDocumentSource.load(self.services.db, document_id, revision)
        store = self.services.store_for(ctx.user_id)
        renderer = self.services.renderer_for(ctx.user_id)
        target: dict[str, Any] = {"ref": target_ref, "type": "document", "title": src.ir.meta.title}
        images = []
        if parsed:
            node = src.node(target_ref)
            target.update(
                type=node.node_type,
                text_raw=node.text_raw,
                text_norm=node.text_norm,
                section_role=node.section_role,
                caption=node.label or node.text_raw,
                regions=[r.model_dump(mode="json") for r in node.geometry.regions],
                attributes=node.attributes,
                geometry=node.geometry.model_dump(mode="json"),
                children=[
                    {**n.model_dump(mode="json"), "ref": n.ref}
                    for n in src.ir.nodes
                    if n.parent_id == node.local_id and n.node_type == "molecule"
                ],
                subtype=node.subtype,
                page_height_pt=next((p.height_pt for p in src.ir.pages if p.page == node.page), 792),
            )
            for region in (
                node.geometry.regions
                if node.node_type in {"figure", "table", "scheme", "figure_part", "chart_panel", "molecule"}
                else []
            ):
                blob = await renderer.crop(document.sha256, region)
                images.append(
                    ImagePart(
                        data_b64=base64.b64encode(await store.get(blob)).decode(),
                        mime="image/png",
                    )
                )
        elif include_document_image:
            blob = await renderer.render_page(document.sha256, 0)
            images.append(
                ImagePart(data_b64=base64.b64encode(await store.get(blob)).decode(), mime="image/png")
            )

        from sqlalchemy import select

        from drugpaper.persistence.models import (
            ArtifactRow,
            CompoundRow,
            LabelClaimRow,
            RGroupAssignmentRow,
            StructureCandidateRow,
        )

        upstreams: dict[str, int] = {}
        name_sources: list[dict[str, Any]] = []
        async with self.services.db.session(ctx.user_id) as session:
            for model, key in (
                (StructureCandidateRow, "structures"),
                (LabelClaimRow, "label_claims"),
                (RGroupAssignmentRow, "assignments"),
            ):
                rows: list[Any] = list(
                    (
                        await session.execute(
                            select(model).where(
                                model.document_id == document_id,
                                model.user_id == ctx.user_id,
                                model.is_active.is_(True),
                            )
                        )
                    ).scalars()
                )
                current_nodes = {n.ref: n for n in src.ir.nodes if n.status != "removed"}
                rows = [r for r in rows if r.target_ref == document_id or r.target_ref in current_nodes]
                if model is StructureCandidateRow:
                    rows = [r for r in rows if r.node_ref in current_nodes]
                    fresh_rows = []
                    for row in rows:
                        if row.source == "opsin":
                            artifact = await session.get(ArtifactRow, row.artifact_id)
                            raw = current_nodes[row.node_ref].text_raw or ""
                            if (
                                artifact is None
                                or not artifact.is_active
                                or artifact.status == "failed"
                                or (artifact.payload or {}).get("source_text_sha256")
                                != hashlib.sha256(raw.encode()).hexdigest()
                                or current_nodes[row.node_ref].section_role != "experimental"
                            ):
                                continue
                            name_sources.append(
                                {
                                    "artifact_id": artifact.id,
                                    "node_ref": row.node_ref,
                                    "text_raw": raw,
                                    "section_role": current_nodes[row.node_ref].section_role,
                                }
                            )
                        fresh_rows.append(row)
                    rows = fresh_rows
                if parsed:
                    prefix = target_ref + "."
                    rows = (
                        [r for r in rows if r.target_ref == target_ref or r.node_ref.startswith(prefix)]
                        if model is StructureCandidateRow
                        else [r for r in rows if r.target_ref == target_ref]
                    )
                payloads = [r.payload for r in rows]
                if model is StructureCandidateRow and parsed:
                    current = {
                        n["ref"]: n.get("attributes", {}).get("structure_id")
                        for n in target.get("children", [])
                        if n.get("status") != "removed"
                    }
                    if target.get("type") == "molecule":
                        current[target_ref] = target.get("attributes", {}).get("structure_id")
                    # A node's projected pointer is authoritative across parent
                    # and local recognition work units; never pick by SMILES or
                    # silently fall back from a failed current recognition.
                    payloads = [
                        p
                        for p in payloads
                        if not current.get(p["node_ref"]) or p["structure_id"] == current[p["node_ref"]]
                    ]
                target[key] = payloads
            target["compounds"] = [
                r.payload
                for r in (
                    await session.execute(
                        select(CompoundRow).where(
                            CompoundRow.document_id == document_id, CompoundRow.user_id == ctx.user_id
                        )
                    )
                ).scalars()
            ]
            target["current_node_refs"] = sorted(current_nodes)
            artifacts = list(
                (
                    await session.execute(
                        select(ArtifactRow).where(
                            ArtifactRow.document_id == document_id,
                            ArtifactRow.user_id == ctx.user_id,
                            ArtifactRow.is_active.is_(True),
                        )
                    )
                ).scalars()
            )
            for artifact in artifacts:
                if (
                    not parsed
                    or artifact.target_ref == target_ref
                    or artifact.target_ref.startswith(target_ref + ".")
                ):
                    upstreams[artifact.id] = artifact.version
        from drugpaper.facts.text_ledger import text_mentions

        target["text_mentions"] = text_mentions(src.ir, artifacts) if not parsed else []
        target["name_sources"] = name_sources
        target["spans"] = [
            {
                "text": n.text_norm or n.text_raw or "",
                "page": r.page,
                "bbox": r.bbox.model_dump(),
                "ref": n.ref,
            }
            for n in src.ir.nodes
            if n.node_type != "molecule"
            for r in n.geometry.regions
            if (n.text_norm or n.text_raw) and len(n.text_norm or n.text_raw or "") < 80
        ]
        from drugpaper.domain.geometry import pt_to_norm
        from drugpaper.persistence.models import ParseRevision

        async with self.services.db.session(ctx.user_id) as session:
            parse_revision = await session.get(ParseRevision, (document_id, src.revision))
            middle_ref = (
                (parse_revision.raw_blob_refs or {}).get("raw_middle_json") if parse_revision else None
            )
        if middle_ref:
            middle = json.loads(await store.get(middle_ref))
            pages = {p.page: p for p in src.ir.pages}

            def collect(value: Any, page: int) -> None:
                if isinstance(value, list):
                    for item in value:
                        collect(item, page)
                elif isinstance(value, dict):
                    if (
                        value.get("type") == "text"
                        and isinstance(value.get("content"), str)
                        and value.get("bbox")
                        and page in pages
                    ):
                        info = pages[page]
                        target["spans"].append(
                            {
                                "text": value["content"],
                                "page": page,
                                "bbox": pt_to_norm(
                                    value["bbox"], (info.width_pt, info.height_pt)
                                ).model_dump(),
                                "ref": target_ref,
                            }
                        )
                    for key in ("para_blocks", "blocks", "lines", "spans"):
                        if key in value:
                            collect(value[key], page)

            for i, page in enumerate(middle.get("pdf_info", [])):
                collect(page, page.get("page_idx", i))
        target["text"] = (
            "\n".join(
                n.text_norm or "" for n in src.ir.nodes if n.node_type in {"paragraph", "caption", "heading"}
            )
            if not parsed
            else ""
        )

        async def call_markush(**kwargs: Any) -> Any:
            return asdict(substitute(**kwargs))

        from drugpaper.tools.builtin.doc_read import make_doc_read_tools
        from drugpaper.tools.builtin.search import make_search_tools

        source_tools = ToolRegistry([*make_doc_read_tools(src), *make_search_tools(src)])
        for name in self.tools.names():
            if name not in source_tools:
                source_tools.register(self.tools.get(name))
        impls: dict[str, ToolFn] = {"markush.substitute": call_markush}
        for name in source_tools.names():
            if name not in impls:

                async def call_tool(_name: str = name, **kwargs: Any) -> Any:
                    from drugpaper.gateway.base import ToolCall

                    result = await source_tools.invoke(
                        ctx, ToolCall(id="skill_tool", name=_name, arguments=kwargs), truncate=False
                    )
                    if not result.ok:
                        raise ValueError(result.error)
                    return result.data

                impls[name] = call_tool
        return SkillContext(
            ctx.user_id,
            document_id,
            src.revision,
            target_ref,
            ctx.run_id,
            ctx.trace_id,
            self.models,
            ToolCaller(frozenset(), impls),
            store,
            SkillBudget(0, 120),
            Materials(
                target=target,
                images=images,
                node_hash=hashlib.sha256(json.dumps(target, sort_keys=True).encode()).hexdigest(),
                upstream_versions=upstreams,
            ),
            extras={
                "prepare_vision": self.prepare_vision,
                "available_artifact_types": [
                    a.artifact_type for a in artifacts if a.target_ref == target_ref
                ],
                "parent_run_id": ctx.run_id,
                "tool_fingerprints": {
                    "chem.name_to_structure": {
                        "base_url": self.services.settings.opsin_base_url,
                        "version": self.services.settings.opsin_model_version,
                        "protocol": "opsin-json-v1",
                    },
                    "molparser.recognize": {
                        "base_url": self.services.settings.molparser_base_url,
                        "rec_only": True,
                        "model_version": self.services.settings.molparser_model_version,
                        "protocol": "p4-multipart-aligned-v1",
                    },
                },
            },
        )


async def bootstrap(services: Services) -> SkillServices:
    from dataclasses import asdict

    tools = ToolRegistry(
        [
            *make_chem_tools(),
            *make_opsin_tools(services.opsin),
            *make_molparser_tools(services.molparser),
            *make_detection_tools(services),
            *make_compound_tools(services),
            *make_figure_fact_tools(services),
        ]
    )
    router = TaskRouter.from_file(services.settings.models_config_path)
    models = RoutedModelClient(router)
    from redis.asyncio import Redis

    from drugpaper.gateway.limiter import EndpointLimiter

    models.limiter = EndpointLimiter(
        Redis.from_url(services.settings.redis_url) if services.settings.redis_url else None,
        services.settings.model_endpoint_concurrency,
    )

    async def markush(**kwargs: Any) -> Any:
        return asdict(substitute(**kwargs))

    registry = SkillRegistry(
        services.db,
        snapshot_root=Path(services.settings.blob_root).parent / "skill_packages",
        known_tools=set(tools.names()) | {"get_node", "get_outline", "grep", "search"},
        known_tasks=router.tasks(),
        tool_impls={"markush.substitute": markush},
    )
    runtime = SkillRuntime(registry, services.db)
    index = CapabilityIndex(registry, tools)
    watcher = SkillWatcher(registry, [(BUILTIN_ROOT, "builtin"), (services.settings.skills_root, "system")])
    bundle = SkillServices(registry, runtime, tools, index, models, services, watcher)
    watcher.on_change = bundle.refresh
    import os

    local_root = os.environ.get("DRUGPAPER_WORKBENCH_PACKAGES")
    if local_root:
        from drugpaper.skills.loader import load_package

        for manifest in (Path(local_root) / "drugpaper/skills/builtin").glob("*/manifest.yaml"):
            skill = load_package(manifest.parent, prompts_root=Path(local_root) / "prompts")
            if not skill.ok:
                raise ValueError(f"局部 Skill 校验失败：{skill.name}: {skill.errors}")
            if skill.manifest and skill.manifest.enabled:
                registry.local_packages[skill.name] = skill
        await bundle.refresh()
    else:
        await watcher.reload(publish=False)
    return bundle
