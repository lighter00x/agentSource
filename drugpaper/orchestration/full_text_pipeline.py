"""P6 main graph: reader → synthesis → chemistry → bounded feedback → projection."""

from __future__ import annotations

import asyncio
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

from drugpaper.agents.reader.graph import ReaderAgent
from drugpaper.agents.reader.notes import ReaderNotes
from drugpaper.agents.reader.synthesize import synthesize
from drugpaper.domain.ids import new_id
from drugpaper.facts.full_text import load_context
from drugpaper.facts.paths import PathProposal, Step, build_steps
from drugpaper.facts.text_measurements import project_text_measurements
from drugpaper.gateway.base import ModelClient
from drugpaper.orchestration.text_pipeline import TextPipeline
from drugpaper.persistence.models import FullTextResultRow, RunRow, utcnow
from drugpaper.profiles.projector import project
from drugpaper.services import Services
from drugpaper.validators.path import validate_path
from drugpaper.validators.projection import validate_projection


class FullTextState(TypedDict, total=False):
    document_id: str
    user_id: str
    run_id: str
    from_page: int | None
    status: str
    reader: dict[str, Any]
    proposal: dict[str, Any]
    steps: list[dict[str, Any]]
    checks: list[dict[str, Any]]
    feedback: list[str]
    attempts: int
    history: list[dict[str, Any]]
    projection: dict[str, Any]
    fingerprint: str


class FullTextPipeline:
    def __init__(self, services: Services, model: ModelClient | None = None) -> None:
        self.services, self.model = services, model
        g: StateGraph[FullTextState] = StateGraph(FullTextState)
        g.add_node("reader_read", self._read)
        g.add_node("path_reason", self._reason)
        g.add_node("step_builder", self._build)
        g.add_node("project", self._project)
        g.add_edge(START, "reader_read")
        g.add_conditional_edges(
            "reader_read",
            lambda s: "continue" if s["status"] == "completed" else "end",
            {"continue": "path_reason", "end": END},
        )
        g.add_edge("path_reason", "step_builder")
        g.add_conditional_edges(
            "step_builder",
            lambda s: "retry" if s["checks"] and s["attempts"] < 3 else "project",
            {"retry": "path_reason", "project": "project"},
        )
        g.add_edge("project", END)
        self.graph = g.compile()

    async def _read(self, state: FullTextState) -> FullTextState:
        progress = await ReaderAgent(self.services, self.model).read(
            state["document_id"], user_id=state["user_id"], from_page=state.get("from_page")
        )
        ctx = await load_context(self.services.db, state["document_id"], user_id=state["user_id"])
        return {
            "reader": progress.model_dump(mode="json"),
            "status": progress.status,
            "fingerprint": ctx.fingerprint,
        }

    async def _reason(self, state: FullTextState) -> FullTextState:
        ctx = await load_context(self.services.db, state["document_id"], user_id=state["user_id"])
        if ctx.fingerprint != state["fingerprint"]:
            raise ValueError("source changed after reading")
        notes = ReaderNotes.model_validate(state["reader"]["notes"])
        proposal = await synthesize(
            self.services, ctx, notes, model=self.model, feedback=state.get("feedback")
        )
        reader = {**state["reader"], "notes": notes.model_dump(mode="json")}
        return {
            "proposal": proposal.model_dump(mode="json"),
            "reader": reader,
            "attempts": state.get("attempts", 0) + 1,
        }

    async def _build(self, state: FullTextState) -> FullTextState:
        ctx = await load_context(self.services.db, state["document_id"], user_id=state["user_id"])
        proposal = PathProposal.model_validate(state["proposal"])
        steps = await asyncio.to_thread(build_steps, proposal, ctx)
        checks = await asyncio.to_thread(validate_path, proposal, steps, ctx)
        feedback = [f"{c.code} {c.field_path}: {c.message}" for c in checks]
        record: dict[str, Any] = {
            "attempt": state["attempts"],
            "proposal": state["proposal"],
            "checks": [c.model_dump() for c in checks],
            "steps": [s.model_dump(mode="json") for s in steps],
        }
        async with self.services.db.session(state["user_id"]) as session:
            session.add(
                RunRow(
                    id=new_id("run"),
                    user_id=state["user_id"],
                    document_id=state["document_id"],
                    run_type="validation",
                    component="path_validate",
                    component_version="0.6.1",
                    parent_run_id=state["run_id"],
                    trace_id=state["run_id"],
                    inputs={"attempt": state["attempts"]},
                    outputs=record,
                    status="needs_review" if checks else "completed",
                    ended_at=utcnow(),
                )
            )
        return {
            "steps": record["steps"],
            "checks": record["checks"],
            "feedback": feedback,
            "history": [*state.get("history", []), record],
        }

    async def _project(self, state: FullTextState) -> FullTextState:
        ctx = await load_context(self.services.db, state["document_id"], user_id=state["user_id"])
        if ctx.fingerprint != state["fingerprint"]:
            raise ValueError("source changed before projection")
        proposal = PathProposal.model_validate(state["proposal"])
        steps = [Step.model_validate(s) for s in state["steps"]]
        result = project(proposal, steps, ctx)
        checks = validate_projection(result, ctx)
        all_checks = [*state["checks"], *[c.model_dump() for c in checks]]
        warnings = [*result.warnings, *state["reader"]["notes"].get("warnings", [])]
        warnings.extend(
            f"upstream needs_review: {a['id']} ({a['type']})"
            for a in ctx.artifacts
            if a["status"] == "needs_review"
        )
        status = (
            "needs_review"
            if all_checks or warnings or any(s.status == "needs_review" for s in steps)
            else "completed"
        )
        result.audit["_audit"]["checks"] = all_checks
        result.audit["_audit"]["warnings"] = list(dict.fromkeys(warnings))
        return {"projection": result.model_dump(mode="json"), "checks": all_checks, "status": status}

    async def run(
        self,
        document_id: str,
        *,
        user_id: str = "default",
        prepare_text: bool = True,
        force: bool = False,
        from_page: int | None = None,
    ) -> dict[str, Any]:
        await load_context(self.services.db, document_id, user_id=user_id)
        if prepare_text:
            prepared = await TextPipeline(self.services).run(document_id, user_id=user_id, force=force)
            if prepared["status"] == "failed":
                return {
                    "document_id": document_id,
                    "status": "failed",
                    "stage": "text_prepare",
                    "preparation": prepared,
                }
        await project_text_measurements(self.services.db, document_id, user_id=user_id)
        run_id = new_id("run")
        initial: FullTextState = {
            "document_id": document_id,
            "user_id": user_id,
            "run_id": run_id,
            "from_page": from_page,
            "attempts": 0,
            "checks": [],
            "history": [],
        }
        async with self.services.db.session(user_id) as session:
            session.add(
                RunRow(
                    id=run_id,
                    user_id=user_id,
                    document_id=document_id,
                    run_type="pipeline",
                    component="full_text",
                    component_version="0.6.1",
                    trace_id=run_id,
                    inputs={"from_page": from_page, "prepare_text": prepare_text},
                )
            )
        try:
            output = dict(await self.graph.ainvoke(initial, config={"recursion_limit": 20}))
        except Exception as exc:
            output = {**initial, "status": "failed", "error": f"{type(exc).__name__}: {exc}"[:500]}
        result_id = None
        async with self.services.db.session(user_id) as session:
            run = await session.get(RunRow, run_id)
            assert run is not None
            if output.get("projection"):
                result_id = new_id("fulltext")
                doc_ctx = await load_context(self.services.db, document_id, user_id=user_id)
                if output["fingerprint"] != doc_ctx.fingerprint:
                    output["status"], output["error"] = "failed", "source changed before result commit"
                    result_id = None
                else:
                    session.add(
                        FullTextResultRow(
                            id=result_id,
                            user_id=user_id,
                            document_id=document_id,
                            revision=doc_ctx.ir.revision,
                            fingerprint=doc_ctx.fingerprint,
                            status=output["status"],
                            payload=output,
                        )
                    )
            output["result_id"] = result_id
            run.status, run.ended_at = output["status"], utcnow()
            run.outputs = output
            run.model = {"calls": output.get("reader", {}).get("notes", {}).get("model_records", [])}
            run.metrics = {
                "synthesis_attempts": output.get("attempts", 0),
                "model_calls": output.get("reader", {}).get("notes", {}).get("model_calls", 0),
                "tool_calls": output.get("reader", {}).get("notes", {}).get("tool_calls", 0),
            }
            run.error = {"message": output["error"]} if output.get("error") else None
        return output
