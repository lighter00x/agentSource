"""One independent, document-scoped FigureAgent per figure."""

from __future__ import annotations

import asyncio
import base64
import copy
import hashlib
import io
import json
import time
from dataclasses import replace
from typing import Any, cast

from langchain_core.runnables import RunnableConfig
from langgraph.errors import NodeCancelledError
from langgraph.graph import END, START, StateGraph
from PIL import Image
from pydantic import BaseModel, Field, create_model
from sqlalchemy import select

from drugpaper.agents.figure.accounting import CallAccounting
from drugpaper.agents.figure.context import (
    extraction_evidence,
    structure_placeholder_inventory,
    structuring_view,
)
from drugpaper.agents.figure.remediation import apply_review_fix, apply_text_layer
from drugpaper.agents.figure.report import FigureReport
from drugpaper.agents.figure.state import FigureAgentState
from drugpaper.agents.figure.visual import VISUAL_POLICY_VERSION
from drugpaper.capabilities.router import CapabilityRouter, DisclosureContext
from drugpaper.domain.geometry import Region
from drugpaper.domain.ids import new_id, parse_ref
from drugpaper.facts.figure_models import ReviewResult
from drugpaper.facts.figure_result import persist_result
from drugpaper.gateway.base import ChatRequest, ImagePart, Message, TextPart
from drugpaper.persistence.models import Document, FigureReportRow, RunRow
from drugpaper.services import Services
from drugpaper.skills.bootstrap import SkillServices
from drugpaper.skills.protocol import SkillContext
from drugpaper.skills.routing import SkillRouting
from drugpaper.tools.protocol import ToolContext
from drugpaper.validators.figure import check_all
from drugpaper.validators.figure.checklist import build, passed, review_material
from drugpaper.validators.figure.codes import CheckResult
from drugpaper.vision.overlay import comprehensive, crop_check
from drugpaper.vision.text_layer import numeric_tokens, words_in_region


class QueryAnswers(BaseModel):
    answers: list[dict[str, Any]] = Field(default_factory=list)


class FigureAgent:
    def __init__(self, services: Services, checkpointer: Any = None):
        self.services = services
        graph = StateGraph(FigureAgentState)
        for name in (
            "classify",
            "plan",
            "prepare",
            "structure",
            "post",
            "check_l1",
            "check_l2",
            "remediate",
            "verify",
            "report",
            "deliver",
            "query",
        ):
            graph.add_node(name, getattr(self, "_" + name))
        graph.add_conditional_edges(
            START,
            lambda s: (
                "query"
                if s.get("mode") == "query"
                else "check_l1"
                if s.get("mode") == "review"
                else "classify"
            ),
        )
        for source, target in [
            ("classify", "plan"),
            ("plan", "prepare"),
            ("prepare", "structure"),
            ("structure", "post"),
            ("post", "check_l1"),
            ("check_l1", "check_l2"),
            ("remediate", "verify"),
            ("query", "report"),
        ]:
            graph.add_edge(source, target)
        graph.add_conditional_edges("check_l2", self._next_check)
        graph.add_conditional_edges("verify", self._next_check)
        graph.add_edge("report", "deliver")
        graph.add_edge("deliver", END)
        self.graph = graph.compile(checkpointer=checkpointer)

    async def _ctx(self, state: FigureAgentState) -> tuple[SkillServices, SkillContext]:
        bundle = await self.services.skills()
        ctx = await bundle.context_for(
            ToolContext(
                user_id=state["user_id"],
                document_id=state["document_id"],
                run_id=state["run_id"],
                trace_id=state["trace_id"],
            ),
            state["node_ref"],
        )
        assert ctx.materials is not None
        ctx.materials.target["blackboard"] = state.get("blackboard", {})
        return bundle, await self._decorate_context(ctx)

    async def _decorate_context(self, ctx: SkillContext) -> SkillContext:
        """Optional developer tracing; normal execution keeps the original context."""
        return ctx

    async def _run_skill(
        self,
        state: FigureAgentState,
        name: str,
        inputs: dict[str, Any] | None = None,
        category: str = "fixed",
        ref: str | None = None,
        force: bool | None = None,
        images: list[ImagePart] | None = None,
        reference_images: list[ImagePart] | None = None,
    ) -> dict[str, Any]:
        bundle, ctx = await self._ctx(state)
        assert ctx.materials is not None
        if ref and ref != state["node_ref"]:
            ctx = await bundle.context_for(
                ToolContext(
                    user_id=state["user_id"],
                    document_id=state["document_id"],
                    run_id=state["run_id"],
                    trace_id=state["trace_id"],
                ),
                ref,
            )
            ctx = await self._decorate_context(ctx)
        assert ctx.materials is not None
        if images is not None:
            ctx.materials.images = images
        if reference_images is not None:
            ctx.extras["reference_images"] = reference_images
        ctx.extras["document_evidence_images"] = extraction_evidence(state.get("exploration", {}))
        if state.get("exploration"):
            ctx.extras["figure_context_limits"] = {
                "images": self.services.settings.figure_exploration_max_context_images,
                "bytes": self.services.settings.figure_exploration_max_request_bytes,
            }
        if name == "table_extract":
            ctx.materials.target["min_text_height_px"] = state.get("material", {}).get(
                "min_text_height_px", 16
            )
        if state.get("round"):
            remaining = 4 - CallAccounting.model_validate(state["accounting"]).remediation_calls
            ctx.extras["max_model_calls"] = max(0, remaining - (category != "remediation"))
            ctx.extras["max_wall_time_s"] = max(0.01, 180 - (time.time() - state["remediation_started"]))
            category = "remediation"
        if category == "fixed" and state.get("exploration", {}).get("status") == "running":
            category = "exploration"
        start = time.perf_counter()
        result = await bundle.runtime.run(
            name,
            ref or state["node_ref"],
            inputs or {},
            ctx,
            force=state.get("force", False) if force is None else force,
        )
        async with self.services.db.session(state["user_id"]) as session:
            row = await session.get(RunRow, result.run_id)
            calls = int((row.metrics or {}).get("model_calls", 0)) if row else 0
        account = CallAccounting.model_validate(state["accounting"])
        account.record(name, calls, int((time.perf_counter() - start) * 1000), category)
        if result.cached:
            account.cached_steps += 1
        state["accounting"] = account.model_dump()
        self.progress = state
        state["trace"] = [
            *state.get("trace", []),
            {
                "step": name,
                "run_id": result.run_id,
                "status": result.status,
                "cached": result.cached,
                "model_calls": calls,
            },
        ]
        state["artifact_ids"] = [*state.get("artifact_ids", []), *[a.artifact_id for a in result.artifacts]]
        if result.status != "completed":
            raise RuntimeError(f"{name}: {result.error}")
        return cast(dict[str, Any], result.artifacts[0].payload)

    async def run(
        self,
        node_ref: str,
        *,
        user_id: str = "default",
        blackboard: dict[str, Any] | None = None,
        mode: str = "full",
        questions: list[str] | None = None,
        force: bool = False,
    ) -> FigureReport:
        if mode not in {"full", "query", "review"}:
            raise ValueError("unknown figure mode")
        parsed = parse_ref(node_ref)
        if parsed is None:
            raise ValueError("figure target must be a node ref")
        state: FigureAgentState = {
            "document_id": parsed[0],
            "node_ref": node_ref,
            "user_id": user_id,
            "blackboard": blackboard or {},
            "mode": mode,
            "questions": questions or [],
            "run_id": new_id("run"),
            "trace_id": new_id("tr"),
            "round": 0,
            "accounting": CallAccounting().model_dump(),
            "artifact_ids": [],
            "trace": [],
            "force": force,
        }
        bundle, ctx = await self._ctx(state)  # owner check precedes report cache
        assert ctx.materials is not None
        from drugpaper.facts.metric_dictionary import MetricDictionary

        stamp = {
            "molecular_contract": 17,
            "visual_policy_version": VISUAL_POLICY_VERSION,
            "exploration": {
                k: v
                for k, v in self.services.settings.model_dump().items()
                if k.startswith("figure_exploration_")
            },
            "metric_dictionary": MetricDictionary().metrics,
            "ref": node_ref,
            "geometry": ctx.materials.target["geometry"],
            "images": [p.data_b64 for p in ctx.materials.images],
            "blackboard": blackboard,
            "mode": mode,
            "questions": questions,
            "version": __import__("drugpaper").__version__,
            "registry": {s.name: s.content_hash for s in await bundle.registry.list_visible()},
            "models": {
                task: bundle.models.router.public_config(task)
                for task in (
                    "visual_classify",
                    "figure_structure",
                    "figure_structure_review",
                    "figure_self_check",
                    "figure_explore",
                )
            },
        }
        fp = hashlib.sha256(json.dumps(stamp, sort_keys=True, default=str).encode()).hexdigest()
        async with self.services.db.session(user_id) as session:
            saved = (
                await session.execute(
                    select(FigureReportRow).where(
                        FigureReportRow.user_id == user_id,
                        FigureReportRow.node_ref == node_ref,
                        FigureReportRow.input_fingerprint == fp,
                        FigureReportRow.mode == mode,
                    )
                )
            ).scalar_one_or_none()
            if (
                saved
                and saved.status in {"ok", "remediated"}
                and not force
                and (
                    mode == "query"
                    or (saved.payload.get("extraction") or {}).get("status")
                    not in {"unclear", "not_a_table", "failed"}
                )
            ):
                if saved.payload.get("backfilled_extraction") and not saved.payload.get("result_file"):
                    metadata, _ = await persist_result(self.services.store_for(user_id), saved.payload)
                    saved.payload = {**saved.payload, "result_file": metadata}
                return FigureReport.model_validate(saved.payload)
            if mode in {"query", "review"}:
                previous = (
                    await session.execute(
                        select(FigureReportRow)
                        .where(
                            FigureReportRow.user_id == user_id,
                            FigureReportRow.node_ref == node_ref,
                            FigureReportRow.mode == "full",
                        )
                        .order_by(FigureReportRow.created_at.desc())
                        .limit(1)
                    )
                ).scalar_one_or_none()
                if previous:
                    old = previous.payload
                    state["classification"] = {"image_type": old["image_type"]}
                    state["extraction"] = old["extraction"]
                    for key in (
                        "pre_backfill_extraction",
                        "backfilled_extraction",
                        "molecular_coverage",
                        "image_products",
                    ):
                        state[key] = old.get(key, [] if key == "image_products" else {})
                    state["artifact_ids"] = old["artifact_ids"]
                    state["material"] = await self._material(state)
                elif mode == "review":
                    raise ValueError("review requires a prior figure report")
        config: RunnableConfig = {
            "configurable": {
                "thread_id": f"{user_id}:figure:{fp}" + (f":{state['run_id']}" if force else "")
            },
            "recursion_limit": 50,
        }
        try:
            snapshot = await self.graph.aget_state(config) if self.graph.checkpointer else None
            result = await self.graph.ainvoke(None if snapshot and snapshot.next else state, config)
            report = FigureReport.model_validate(result["report"])
        except NodeCancelledError as exc:
            raise asyncio.CancelledError("figure worker interrupted") from exc
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            state = getattr(self, "progress", state)
            report = FigureReport(
                report_id=new_id("fr"),
                document_id=parsed[0],
                node_ref=node_ref,
                mode=mode,
                status="needs_review" if state.get("extraction") else "failed",
                image_type=state.get("classification", {}).get("image_type", "other"),
                exploration=state.get("exploration", {}),
                extraction=state.get("extraction", {}),
                pre_backfill_extraction=state.get("pre_backfill_extraction", {}),
                backfilled_extraction=state.get("backfilled_extraction", {}),
                molecular_coverage=state.get("molecular_coverage", {}),
                image_products=state.get("image_products", []),
                checks=[CheckResult.model_validate(c) for c in state.get("checks", [])],
                review=ReviewResult.model_validate(state.get("review", {})),
                unchecked=state.get("unchecked", []),
                child_reports=state.get("child_reports", []),
                run_id=state["run_id"],
                open_questions=[f"{type(exc).__name__}: {exc}"],
                accounting=CallAccounting.model_validate(state["accounting"]),
                artifact_ids=state["artifact_ids"],
                trace=state["trace"],
            )
        if report.backfilled_extraction:
            report.result_file, _ = await persist_result(
                self.services.store_for(user_id), report.model_dump()
            )
        async with self.services.db.session(user_id) as session:
            existing = (
                await session.execute(
                    select(FigureReportRow).where(
                        FigureReportRow.user_id == user_id,
                        FigureReportRow.node_ref == node_ref,
                        FigureReportRow.input_fingerprint == fp,
                        FigureReportRow.mode == mode,
                    )
                )
            ).scalar_one_or_none()
            if existing:
                existing.status = report.status
                existing.payload = report.model_dump()
            else:
                session.add(
                    FigureReportRow(
                        id=report.report_id,
                        user_id=user_id,
                        document_id=parsed[0],
                        node_ref=node_ref,
                        mode=mode,
                        status=report.status,
                        input_fingerprint=fp,
                        payload=report.model_dump(),
                    )
                )
        return report

    async def _classify(self, state: FigureAgentState) -> FigureAgentState:
        state["classification"] = await self._run_skill(state, "visual_classify")
        if state["classification"]["confidence"] < 0.6:
            state["classification"] = await self._run_skill(state, "visual_reclassify", category="exception")
        return state

    async def _plan(self, state: FigureAgentState) -> FigureAgentState:
        bundle = await self.services.skills()
        skills = {s.name: s for s in await bundle.registry.list_visible()}
        state["chain"] = SkillRouting.from_file(
            self.services.settings.routing_config_path, skills
        ).resolve_chain(state["classification"]["image_type"])
        if not state["chain"]:
            raise ValueError("classification has no active skill chain")
        return state

    async def _prepare(self, state: FigureAgentState) -> FigureAgentState:
        _, original_ctx = await self._ctx(state)
        assert original_ctx.materials is not None
        state["image_products"] = []
        for segment, part in enumerate(original_ctx.materials.images):
            raw = base64.b64decode(part.data_b64 or "")
            image = Image.open(io.BytesIO(raw))
            state["image_products"].append(
                {
                    "stage": "original",
                    "segment": segment,
                    "page": original_ctx.materials.target["regions"][segment]["page"],
                    "blob_ref": await self.services.store_for(state["user_id"]).put(raw, "image/png"),
                    "width": image.width,
                    "height": image.height,
                    "status": "source_only",
                }
            )
        for name in state["chain"]:
            if name not in {
                "molecule_detect",
                "molecule_recognize",
                "som_render",
                "label_bind",
                "panel_split",
            }:
                continue
            if name == "molecule_recognize":
                _, ctx = await self._ctx(state)
                assert ctx.materials is not None
                for child in ctx.materials.target.get("children", []):
                    if child["status"] != "removed":
                        await self._run_skill(state, name, ref=child["ref"])
            elif name != "panel_split":
                result = await self._run_skill(state, name)
                if name == "som_render":
                    for segment in result.get("images", []):
                        state["image_products"].append(
                            {
                                "stage": "model_input_numbered",
                                "segment": segment["parent_region_index"],
                                "page": original_ctx.materials.target["regions"][
                                    segment["parent_region_index"]
                                ]["page"],
                                "blob_ref": segment["blob_ref"],
                                "status": "input_only",
                            }
                        )
        state["material"] = await self._material(state)
        return state

    async def _review_reference_assignments(self, state: dict, arguments: dict) -> dict:
        from drugpaper.agents.figure.reference_review import review_reference_assignments

        return await review_reference_assignments(self, state, arguments)

    async def _material(self, state: FigureAgentState) -> dict[str, Any]:
        _, ctx = await self._ctx(state)
        assert ctx.materials is not None
        target = ctx.materials.target
        async with self.services.db.session(state["user_id"]) as session:
            doc = await session.get(Document, state["document_id"])
        assert doc
        pdf = await self.services.store_for(state["user_id"]).get(doc.sha256)
        words = [
            word
            for region in target.get("regions", [])
            for word in words_in_region(pdf, Region.model_validate(region))
        ]
        tokens = numeric_tokens(words)
        page_spans: list[dict[str, Any]] = [
            {"text": word.text, "bbox": word.bbox.model_dump(), "page": word.page} for word in words
        ]
        # Convert page coordinates to the crop's coordinates for unique cell matching.
        local_spans = []
        for region in target.get("regions", []):
            b = region["bbox"]
            for word in page_spans:
                if word["page"] != region["page"]:
                    continue
                wb = word["bbox"]
                local_spans.append(
                    {
                        **word,
                        "bbox": {
                            "x0": (wb["x0"] - b["x0"]) / (b["x1"] - b["x0"]),
                            "y0": (wb["y0"] - b["y0"]) / (b["y1"] - b["y0"]),
                            "x1": (wb["x1"] - b["x0"]) / (b["x1"] - b["x0"]),
                            "y1": (wb["y1"] - b["y0"]) / (b["y1"] - b["y0"]),
                        },
                    }
                )
        return {
            "min_text_height_px": min(
                (
                    w.bbox.height
                    * float(ctx.materials.target.get("page_height_pt", 792))
                    * self.services.settings.crop_dpi
                    / 72
                    for w in words
                ),
                default=16,
            ),
            "text_tokens": tokens,
            "text_spans": local_spans,
            "caption": target.get("caption", ""),
            "source_text": [
                target.get("caption", ""),
                *tokens,
                *state.get("blackboard", {}).get("references", {}).get(state["node_ref"], []),
            ],
            "known_labels": state.get("blackboard", {}).get("labels", []),
            "structures": target.get("structures", []),
            "boxes": [
                {
                    "mol_id": n["attributes"]["reading_index"],
                    "bbox": n["geometry"]["local_bbox"],
                    "node_ref": n["ref"],
                    "segment": n["geometry"].get("parent_region_index", 0),
                }
                for n in target.get("children", [])
                if n["status"] != "removed"
            ],
            "molecular_screened": True,
            "expects_structures": state.get("classification", {}).get("has_chemical_structures", False),
            "image_type": state.get("classification", {}).get("image_type", "other"),
            "expected_panels": state.get("classification", {}).get("panel_count")
            if state.get("classification", {}).get("image_type") == "multi_panel"
            else None,
            "prior_type": target["type"] if target["type"] in {"table", "scheme"} else None,
        }

    async def _structure(self, state: FigureAgentState) -> FigureAgentState:
        if self.services.settings.figure_exploration_enabled and not state.get("round"):
            from drugpaper.agents.figure.exploration import explore

            return await explore(self, state)
        await self._structure_once(state)
        from drugpaper.agents.figure.structure_review import enabled, review_structure

        if enabled(state):
            log = state.setdefault("exploration", {})
            log.update(structure_review_required=True, extraction_version=1)
            try:
                audit = await review_structure(self, state)
                log["status"] = "completed" if audit["status"] == "passed" else "needs_review"
            except Exception as exc:
                log.update(
                    status="needs_review", structure_audit={"status": "review_error", "error": str(exc)}
                )
        return state

    async def _exploration_event(self, state: FigureAgentState, event: dict[str, Any]) -> None:
        state.setdefault("trace", []).append({"step": "figure_exploration", **event})
        self.progress = state

    async def _structure_once(self, state: FigureAgentState) -> FigureAgentState:
        bundle, ctx = await self._ctx(state)
        assert ctx.materials is not None
        skills = {s.name: s for s in await bundle.registry.list_visible()}
        chosen = [
            name
            for name in state["chain"]
            if name in skills
            and skills[name].manifest is not None
            and cast(Any, skills[name].manifest).vision_structuring
        ]
        if len(chosen) != 1:
            raise ValueError("D38: exactly one structuring skill required")
        # Validate provenance and freshness instead of silently falling back to
        # an unnumbered original when the numbered artifact is absent or stale.
        if "som_render" in state["chain"]:
            await bundle.prepare_vision(ctx)
        images = list(ctx.materials.images)
        references = ctx.extras.get("reference_images")
        inputs = {
            "text_tokens": state["material"]["text_tokens"],
            "caption": state["material"]["caption"],
            "references": state["material"]["source_text"],
            "classification": state["classification"],
            "document_evidence": structuring_view(extraction_evidence(state.get("exploration", {}))),
            "information_gaps": state.get("exploration", {}).get("gaps", []),
            "structure_inventory": structure_placeholder_inventory(ctx.materials),
            "reference_substituent_evidence": state.get("exploration", {}).get("reference_assignments", []),
        }
        if state.get("exploration", {}).get("regeneration_context"):
            inputs["regeneration_context"] = state["exploration"]["regeneration_context"]
        state["extraction"] = await self._run_skill(
            state,
            chosen[0],
            inputs,
            images=images,
            reference_images=references,
            category="remediation"
            if state.get("round")
            else "exploration"
            if state.get("exploration")
            else "fixed",
            force=True if state.get("exploration") else None,
        )
        if state.get("exploration", {}).get("reference_assignments") and not state["exploration"].get(
            "regeneration_context"
        ):
            from drugpaper.agents.figure.reference_assignments import apply_reference_assignments

            log = state["exploration"]
            log["applied_evidence_ids"] = [e["id"] for e in extraction_evidence(log)]
            state["extraction"] = apply_reference_assignments(state["extraction"], log)
        return state

    async def _post(self, state: FigureAgentState) -> FigureAgentState:
        from drugpaper.agents.figure.structure_review import (
            audit_passed,
            defer_delivery,
            enabled,
            normalize_position_fields,
        )

        normalize_position_fields(state)
        if enabled(state) and not audit_passed(state):
            return defer_delivery(state)
        structure_snapshot = copy.deepcopy(state["extraction"]) if enabled(state) else None
        data = state["extraction"]
        state["pre_backfill_extraction"] = copy.deepcopy(data)
        kind = state["classification"]["image_type"]
        from drugpaper.agents.figure.reference_assignments import blocked_instance_paths

        filled = await self._run_skill(
            state,
            "mol_backfill",
            {
                "contract": "universal",
                "extraction_payload": data,
                "structure_source_scope": "current_figure",
                "blocked_instance_paths": blocked_instance_paths(data, state.get("exploration", {})),
            },
            force=bool(state.get("round")) or None,
        )
        from drugpaper.agents.figure.reference_assignments import enforce_reference_assignments

        enforce_reference_assignments(filled, data, state.setdefault("exploration", {}))
        # The Skill owns canonical coverage; the agent uses only its ID-only inventory for review.
        state["extraction"]["molecule_inventory"] = filled["payload"]["molecule_inventory"]
        state["backfilled_extraction"] = filled["payload"]
        state["backfilled_extraction"]["molecule_objects"] = filled.get("molecule_objects", [])
        state["molecular_coverage"] = filled["coverage"]
        state["image_products"] = [
            p for p in state.get("image_products", []) if p["stage"] == "model_input_numbered"
        ] + filled["image_products"]
        state["material"]["backfill_errors"] = filled["errors"]
        reference_bindings: list[dict[str, Any]] = []
        reference_3d: list[bool] = []

        def collect_bindings(value: Any) -> None:
            if isinstance(value, list):
                for child in value:
                    collect_bindings(child)
            elif isinstance(value, dict):
                if binding := value.get("reference_binding"):
                    reference_bindings.append(
                        {k: binding[k] for k in ("reference", "source_ref", "binding_method")}
                    )
                    reference_3d.append(
                        value.get("representation") == "3d_stick_models"
                        or data.get("representation") == "3d_stick_models"
                    )
                for key, child in value.items():
                    if key != "reference_binding":
                        collect_bindings(child)

        collect_bindings(filled["payload"])
        state["material"]["reference_bindings"] = list(
            {(b["reference"], b["source_ref"]): b for b in reference_bindings}.values()
        )
        state["material"]["reference_3d_complete"] = bool(reference_3d) and all(reference_3d)
        sar = None
        if "header_rows" in data:
            mapped = await self._run_skill(
                state,
                "measurement_map",
                {"grid": data},
                category="remediation" if state.get("round") else "exception",
                force=bool(state.get("round")) or None,
            )
            state["material"]["measurements"] = mapped["measurements"]
            if kind == "sar_table":
                sar = await self._run_skill(
                    state,
                    "sar_record",
                    {"grid": data, "require_explicit_instances": True},
                    force=bool(state.get("round")) or None,
                )
            else:
                sar = None
        elif "x_axis" in data:
            mapped = await self._run_skill(
                state,
                "chart_measurements",
                {"dataset": data},
                category="remediation" if state.get("round") else "exception",
            )
            state["material"]["measurements"] = mapped["measurements"]
        if sar is not None:
            state["backfilled_extraction"]["sar_records"] = sar
            for instance in state["backfilled_extraction"].get("compound_instances", []):
                record = next(
                    (
                        r
                        for r in sar["rows"]
                        if r.get("reference") == instance.get("reference")
                        and r.get("row_index") == instance.get("row_index")
                    ),
                    None,
                )
                if record:
                    instance["properties"] = record.get("properties", [])
        for molecule in data.get("molecules", []):
            box = next(
                (b for b in state["material"].get("boxes", []) if b["mol_id"] == molecule.get("mol_id")), None
            )
            candidate = next(
                (
                    s
                    for s in state["material"].get("structures", [])
                    if box and s["node_ref"] == box["node_ref"]
                ),
                None,
            )
            if candidate:
                molecule["inchikey"] = candidate.get("inchikey")
        if kind == "multi_panel" and data.get("panels") and ".panel" not in state["node_ref"]:
            await self._run_skill(state, "panel_split", {"panels": data["panels"]}, category="exception")
            state["child_reports"] = []
            for i, _ in enumerate(data["panels"]):
                child = await FigureAgent(self.services, self.graph.checkpointer).run(
                    state["node_ref"] + f".panel{i:03d}",
                    user_id=state["user_id"],
                    blackboard=state.get("blackboard"),
                )
                state["child_reports"].append(child.model_dump())
                accounting = CallAccounting.model_validate(state["accounting"])
                accounting.exception_calls += child.accounting.total
                state["accounting"] = accounting.model_dump()
        # Directional scheme topology is retained with figure evidence.
        if kind.startswith("synthetic_scheme"):
            await self._persist_routes(state)
        if structure_snapshot is not None:
            state["extraction"] = structure_snapshot
        return state

    async def _persist_routes(self, state: FigureAgentState) -> None:
        payload = await self._run_skill(
            state,
            "scheme_relations",
            {"extraction": state["backfilled_extraction"]},
            category="remediation" if state.get("round") else "exception",
        )
        state["material"]["backfill_errors"].extend(payload.get("errors", []))
        state["backfilled_extraction"]["route_errors"] = payload.get("errors", [])
        state["extraction"]["relations"] = payload["relations"]
        state["backfilled_extraction"]["relations"] = payload["relations"]
        state["material"]["route_labels"] = [
            label for edge in payload["relations"] for label in (edge["source"], edge["target"])
        ]

    async def _check_l1(self, state: FigureAgentState) -> FigureAgentState:
        context = {**state["material"], "extraction": state["extraction"]}
        checks = check_all(context)
        state["checks"] = [c.model_dump() for c in checks]
        state["checklist"], state["unchecked"] = build(context, checks)
        return state

    async def _review(self, state: FigureAgentState, category: str = "fixed") -> None:
        _, ctx = await self._ctx(state)
        assert ctx.materials is not None
        from drugpaper.render.images import limit_max_side

        images = []
        for part in ctx.materials.images:
            data, mime = limit_max_side(base64.b64decode(part.data_b64 or ""), 1600)
            images.append(ImagePart(data_b64=base64.b64encode(data).decode(), mime=mime))
        if images:
            original = Image.open(io.BytesIO(base64.b64decode(images[0].data_b64 or ""))).convert("RGB")
            drawn = comprehensive(original, {**state["material"], "extraction": state["extraction"]})
            stream = io.BytesIO()
            drawn.save(stream, format="PNG")
            images.append(ImagePart(data_b64=base64.b64encode(stream.getvalue()).decode(), mime="image/png"))
            for item in state["checklist"][:6]:
                crop = crop_check(original, state["extraction"], item["field_path"])
                if crop and min(crop.size) > 0:
                    stream = io.BytesIO()
                    crop.save(stream, format="PNG")
                    images.append(
                        ImagePart(data_b64=base64.b64encode(stream.getvalue()).decode(), mime="image/png")
                    )
        payload = await self._run_skill(
            state,
            "figure_self_check",
            {
                "checklist": state["checklist"],
                "extraction": review_material(state["extraction"], state["checklist"]),
                "focus": "remediation" if category == "remediation" else "all four dimensions",
            },
            category=category,
            images=images,
            force=True if category == "remediation" else None,
        )
        state["review"] = ReviewResult.model_validate(payload).model_dump()

    async def _check_l2(self, state: FigureAgentState) -> FigureAgentState:
        await self._review(state)
        return state

    @staticmethod
    def _next_check(state: FigureAgentState) -> str:
        if state.get("exploration", {}).get("structure_review_required"):
            # The FigureAgent already owns evidence collection and local repairs.
            # Preserve its extraction instead of invoking the legacy whole-output remediation.
            return "report"
        review = ReviewResult.model_validate(state.get("review", {}))
        if (
            state.get("gave_up")
            or passed(review, state.get("checklist", []))
            and not any(c["severity"] == "error" for c in state.get("checks", []))
        ):
            return "report"
        account = CallAccounting.model_validate(state["accounting"])
        if (
            state.get("round", 0) >= 2
            or account.remediation_calls >= 3
            or time.time() - state.get("remediation_started", time.time()) >= 180
        ):
            return "report"
        return "remediate"

    async def _remediate(self, state: FigureAgentState) -> FigureAgentState:
        self.progress = state
        state.setdefault("remediation_started", time.time())
        try:
            async with asyncio.timeout(max(0.01, 180 - (time.time() - state["remediation_started"]))):
                return await self._remediate_with_budget(state)
        except TimeoutError:
            state["gave_up"] = True
            return state

    async def _remediate_with_budget(self, state: FigureAgentState) -> FigureAgentState:
        state.setdefault("remediation_started", time.time())
        state["round"] = state.get("round", 0) + 1
        bundle, _ = await self._ctx(state)
        codes = [c["code"] for c in state.get("checks", [])]
        review = ReviewResult.model_validate(state["review"])
        codes.append(
            "C_REVIEW_INCORRECT" if any(i.verdict == "incorrect" for i in review.items) else "C_REVIEW_UNSURE"
        )
        disclosure = CapabilityRouter(bundle.index).preselect(
            DisclosureContext(
                user_id=state["user_id"],
                audience="figure_agent",
                failed_checks=codes,
                token_budget=800,
                invocations=["action_enum"],
                max_candidates=4,
            )
        )
        names = [item.name for item in disclosure.items]
        if not names:
            state["gave_up"] = True
            return state
        # The output enum is limited to the actual disclosed actions, never the whole registry.
        decision = create_model(
            "RemediationDecision",
            action=(str, Field(json_schema_extra=cast(Any, {"enum": names}))),
            params=(dict[str, Any], Field(default_factory=dict)),
            reason=(str, ""),
        )
        start = time.perf_counter()
        account = CallAccounting.model_validate(state["accounting"])
        account.record("remediate_decide", 1, 0, "remediation")
        state["accounting"] = account.model_dump()
        response = await bundle.models.chat(
            ChatRequest(
                messages=[
                    Message(
                        role="system",
                        content="Choose one disclosed action only. Do not execute tools or code.",
                    ),
                    Message(
                        role="user",
                        content=json.dumps(
                            {
                                "node_ref": state["node_ref"],
                                "document_figures": list(state.get("blackboard", {}).get("captions", {})),
                                "cards": disclosure.text,
                                "checks": state["checks"],
                                "review": state["review"],
                                "allowed_actions": names,
                            },
                            ensure_ascii=False,
                        ),
                    ),
                ],
                response_schema=decision,
            ),
            task="remediate_decide",
        )
        selected = response.parsed.model_dump() if response.parsed else json.loads(response.content or "{}")
        account = CallAccounting.model_validate(state["accounting"])
        account.record("remediate_decide", 0, int((time.perf_counter() - start) * 1000), "remediation")
        state["accounting"] = account.model_dump()
        if selected.get("action") not in names:
            state["gave_up"] = True
            state["trace"].append({"step": "remediation_rejected", "reason": "undisclosed action"})
            return state
        state["action"] = selected
        state["trace"].append(
            {
                "step": "remediate",
                "disclosed": names,
                "action": selected["action"],
                "reason": selected.get("reason"),
            }
        )
        await self._run_skill(
            state,
            selected["action"],
            {"params": selected.get("params", {}), "reason": selected.get("reason", "")},
            category="remediation",
            force=True,
        )
        remaining = 4 - CallAccounting.model_validate(state["accounting"]).remediation_calls
        if (
            selected["action"] in {"retranscribe_cells", "rebind_labels", "reslice_table", "reclassify"}
            and remaining < 2
        ):
            state["gave_up"] = True
            return state
        try:
            await self._execute_action(state, selected)
        except (PermissionError, ValueError, KeyError, IndexError) as exc:
            state["gave_up"] = True
            state["trace"].append(
                {"step": "remediation_rejected", "reason": f"{type(exc).__name__}: {exc}"[:300]}
            )
        return state

    async def _execute_action(self, state: FigureAgentState, selected: dict[str, Any]) -> None:
        action = selected["action"]
        params = selected.get("params", {})
        bundle, ctx = await self._ctx(state)
        assert ctx.materials is not None
        action_skill = await bundle.registry.resolve(action, state["user_id"])
        assert action_skill.manifest is not None
        ctx.tools = replace(ctx.tools, allowed=frozenset(action_skill.manifest.tools))
        if action == "give_up":
            state["gave_up"] = True
            return
        if action == "apply_review_fix":
            state["extraction"] = apply_review_fix(
                state["extraction"], ReviewResult.model_validate(state["review"]), params.get("paths")
            )
        elif action == "apply_text_layer":
            state["extraction"] = apply_text_layer(
                state["extraction"], params.get("cells", []), state["material"]["text_spans"]
            )
        elif action == "fix_unit":
            # Unit corrections must be supported by the closed review, never arbitrary parameters.
            state["extraction"] = apply_review_fix(
                state["extraction"], ReviewResult.model_validate(state["review"]), params.get("paths")
            )
        elif action in {"retranscribe_cells", "rebind_labels", "recalibrate_axes"}:
            if action == "recalibrate_axes":
                allowed = [
                    i.field_path
                    for i in ReviewResult.model_validate(state["review"]).items
                    if i.verdict == "incorrect"
                ]
                if allowed:
                    state["extraction"] = apply_review_fix(
                        state["extraction"], ReviewResult.model_validate(state["review"]), allowed
                    )
                from drugpaper.skills.figure import chart_post

                state["extraction"] = await chart_post(state["extraction"], ctx)
            else:
                # One targeted transcription call and one verify call fit the four-call total cap.
                review_schema = ReviewResult
                started = time.perf_counter()
                response = await bundle.models.chat(
                    ChatRequest(
                        messages=[
                            Message(
                                role="user",
                                content=[
                                    TextPart(
                                        text=json.dumps(
                                            {
                                                "action": action,
                                                "params": params,
                                                "extraction": state["extraction"],
                                                "review": state["review"],
                                                "instruction": "Only re-read the disputed existing fields. Return closed incorrect items with suggested_value or unsure. Do not change unrelated fields.",
                                            },
                                            ensure_ascii=False,
                                        )
                                    ),
                                    *ctx.materials.images,
                                ],
                            )
                        ],
                        response_schema=review_schema,
                    ),
                    task="figure_remediate_vision",
                )
                correction = ReviewResult.model_validate(
                    response.parsed.model_dump() if response.parsed else json.loads(response.content or "{}")
                )
                allowed_paths = [
                    i.field_path
                    for i in ReviewResult.model_validate(state["review"]).items
                    if i.verdict != "correct"
                ]
                allowed = [
                    i.field_path
                    for i in correction.items
                    if any(i.field_path == p or i.field_path.startswith(p + ".") for p in allowed_paths)
                ]
                state["extraction"] = apply_review_fix(state["extraction"], correction, allowed)
                account = CallAccounting.model_validate(state["accounting"])
                account.record(action, 1, int((time.perf_counter() - started) * 1000), "remediation")
                state["accounting"] = account.model_dump()
        elif action == "redetect":
            await self._run_skill(
                state, "molecule_detect", {"preset": "high_recall"}, category="remediation", force=True
            )
            _, newctx = await self._ctx(state)
            assert newctx.materials is not None
            for child in newctx.materials.target.get("children", []):
                await self._run_skill(
                    state, "molecule_recognize", ref=child["ref"], category="remediation", force=True
                )
        elif action == "recognize_region":
            allowed_boxes = {b["mol_id"]: b["node_ref"] for b in state["material"]["boxes"]}
            for mol_id in params.get("box_ids", []):
                if mol_id not in allowed_boxes:
                    raise PermissionError("unscoped recognition box")
                await self._run_skill(
                    state, "molecule_recognize", ref=allowed_boxes[mol_id], category="remediation", force=True
                )
        elif action == "reslice_table":
            state["extraction"] = await self._run_skill(
                state, "table_extract", {"max_side": 1000}, category="remediation", force=True
            )
        elif action == "reclassify":
            state["classification"] = await self._run_skill(
                state, "visual_classify", category="remediation", force=True
            )
            await self._plan(state)
            # Reclassification material remains a question when a full new chain exceeds the budget.
            await self._prepare(state)
            await self._structure(state)
        elif action == "split_panels":
            panels = params.get("panels", [])
            await self._run_skill(
                state, "panel_split", {"panels": panels}, category="remediation", force=True
            )
            state["gave_up"] = not bool(panels)
        elif action in {"action_grep", "action_search"}:
            result = await ctx.tools.call(action.removeprefix("action_"), **params)
            state["material"]["source_text"].append(json.dumps(result, ensure_ascii=False))
        elif action == "action_get_figure_report":
            target = params.get("node_ref", "")
            if (
                parse_ref(target) is None
                or cast(tuple[str, int, str], parse_ref(target))[0] != state["document_id"]
            ):
                raise PermissionError("report outside document")
            async with self.services.db.session(state["user_id"]) as session:
                row = (
                    await session.execute(
                        select(FigureReportRow)
                        .where(
                            FigureReportRow.user_id == state["user_id"], FigureReportRow.node_ref == target
                        )
                        .order_by(FigureReportRow.created_at.desc())
                        .limit(1)
                    )
                ).scalar_one_or_none()
            state["material"]["related_report"] = row.payload if row else {"status": "pending"}
        else:
            raise PermissionError("unsupported action")
        if action in {"redetect", "recognize_region"}:
            state["material"] = {**state["material"], **await self._material(state)}
        # Post-processing versions are recomputed after corrections, with source tags retained.
        await self._post(state)

    async def _verify(self, state: FigureAgentState) -> FigureAgentState:
        await self._check_l1(state)
        if state.get("gave_up"):
            return state
        account = CallAccounting.model_validate(state["accounting"])
        if account.remediation_calls >= 4:
            state["gave_up"] = True
            return state
        async with asyncio.timeout(max(0.01, 180 - (time.time() - state["remediation_started"]))):
            await self._review(state, "remediation")
        return state

    async def _query(self, state: FigureAgentState) -> FigureAgentState:
        bundle, ctx = await self._ctx(state)
        assert ctx.materials is not None
        started = time.perf_counter()
        response = await bundle.models.chat(
            ChatRequest(
                messages=[
                    Message(
                        role="system",
                        content="Answer only the targeted reconciliation questions from this figure. Preserve uncertainty and evidence_text. Do not rewrite unrelated facts.",
                    ),
                    Message(
                        role="user",
                        content=[
                            TextPart(
                                text=json.dumps(
                                    {
                                        "questions": state["questions"],
                                        "previous": state.get("extraction", {}),
                                        "blackboard": state["blackboard"],
                                    },
                                    ensure_ascii=False,
                                )
                            ),
                            *ctx.materials.images,
                        ],
                    ),
                ],
                response_schema=QueryAnswers,
            ),
            task="figure_query",
        )
        answer = response.parsed.model_dump() if response.parsed else json.loads(response.content or "{}")
        state["query_answers"] = QueryAnswers.model_validate(answer).answers
        account = CallAccounting.model_validate(state["accounting"])
        account.record("figure_query", 1, int((time.perf_counter() - started) * 1000), "exception")
        state["accounting"] = account.model_dump()
        return state

    async def _report(self, state: FigureAgentState) -> FigureAgentState:
        review = ReviewResult.model_validate(state.get("review", {}))
        checks = [CheckResult.model_validate(c) for c in state.get("checks", [])]
        sar = state.get("backfilled_extraction", {}).get("sar_records")
        sar_incomplete = sar is not None and sar.get("status") != "completed"
        ok = (
            passed(review, state.get("checklist", []))
            and not any(c.severity == "error" for c in checks)
            and not state.get("gave_up")
            and not state.get("material", {}).get("backfill_errors")
            and state.get("extraction", {}).get("status") not in {"unclear", "not_a_table", "failed"}
            and not sar_incomplete
            and state.get("exploration", {}).get("status", "completed") == "completed"
        )
        if state.get("mode") == "query":
            ok = bool(state.get("query_answers"))
        report = FigureReport(
            report_id=new_id("fr"),
            document_id=state["document_id"],
            node_ref=state["node_ref"],
            mode=state.get("mode", "full"),
            image_type=state.get("classification", {}).get("image_type", "other"),
            status=("remediated" if state.get("round") else "ok") if ok else "needs_review",
            confidence=state.get("classification", {}).get("confidence"),
            artifact_ids=state["artifact_ids"],
            extraction=state.get("extraction", {}),
            pre_backfill_extraction=state.get("pre_backfill_extraction", {}),
            backfilled_extraction=state.get("backfilled_extraction", {}),
            molecular_coverage=state.get("molecular_coverage", {}),
            image_products=state.get("image_products", []),
            checks=checks,
            review=review,
            unchecked=state.get("unchecked", []),
            open_questions=(
                state.get("exploration", {}).get("unresolved", [])
                + (
                    ["FigureAgent exploration: " + str(state["exploration"].get("stop_reason"))]
                    if state.get("exploration", {}).get("status") == "needs_review"
                    else []
                )
                + [c.message or c.code for c in checks]
                + [f"{i.field_path}: {i.reason or i.verdict}" for i in review.items if i.verdict != "correct"]
                + (["Some requested fields remain unchecked"] if state.get("unchecked") else [])
                + (
                    ["SAR full structures are not completely assembled; inspect sar_records"]
                    if sar_incomplete
                    else []
                )
                + (
                    ["Extraction explicitly returned an unusable/unclear status"]
                    if state.get("extraction", {}).get("status") in {"unclear", "not_a_table", "failed"}
                    else []
                )
            )
            if not ok
            else [],
            accounting=CallAccounting.model_validate(state["accounting"]),
            trace=state["trace"],
            exploration=state.get("exploration", {}),
            query_answers=state.get("query_answers", []),
            child_reports=state.get("child_reports", []),
            run_id=state["run_id"],
        )
        state["report"] = report.model_dump()
        return state

    async def _deliver(self, state: FigureAgentState) -> FigureAgentState:
        from drugpaper.facts.figure_result import build_result
        from drugpaper.facts.flowchart_delivery import summarize_result

        report = state.get("report", {})
        if report.get("backfilled_extraction"):
            state["summary_result"] = summarize_result(build_result(report))
        return state
