from __future__ import annotations

import hashlib
import json
import operator
from typing import Annotated, Any, TypedDict, cast

from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph
from langgraph.types import Send

from drugpaper.agent.checkpoint import open_checkpointer
from drugpaper.agents.coordinator.blackboard import build_v1, build_v2
from drugpaper.agents.coordinator.dispatch import Coordinator
from drugpaper.agents.coordinator.reconcile import questions
from drugpaper.agents.figure.report import FigureReport
from drugpaper.domain.ids import new_id
from drugpaper.services import Services
from drugpaper.tools.protocol import ToolContext


class FigurePipelineState(TypedDict, total=False):
    document_id: str
    user_id: str
    blackboard: dict[str, Any]
    blackboard_v2: dict[str, Any]
    targets: list[str]
    target_ref: str
    reports: Annotated[list[dict[str, Any]], operator.add]
    queries: Annotated[list[dict[str, Any]], operator.add]
    questions: dict[str, list[str]]
    result: dict[str, Any]
    force: bool


class FigurePipeline:
    def __init__(self, services: Services):
        self.services = services

    async def run(self, document_id: str, *, user_id: str = "default", force: bool = False) -> dict[str, Any]:
        bundle = await self.services.skills()
        await bundle.context_for(
            ToolContext(
                user_id=user_id, document_id=document_id, run_id=new_id("run"), trace_id=new_id("tr")
            ),
            document_id,
        )
        async with open_checkpointer(self.services.settings.database_url) as saver:
            coordinator = Coordinator(self.services, saver)

            async def blackboard(state: FigurePipelineState) -> dict[str, Any]:
                bundle = await self.services.skills()
                await bundle.context_for(
                    ToolContext(
                        user_id=user_id, document_id=document_id, run_id=new_id("run"), trace_id=new_id("tr")
                    ),
                    document_id,
                )
                src = await IrDocumentSource.load(self.services.db, document_id)
                nodes = sorted(
                    [
                        n
                        for n in src.ir.nodes
                        if n.node_type in {"scheme", "table", "figure"}
                        and n.status != "removed"
                        and n.geometry.regions
                    ],
                    key=lambda n: (n.page, 0 if n.node_type == "scheme" else 1, n.local_id),
                )
                return {"blackboard": build_v1(src.ir), "targets": [n.ref for n in nodes]}

            def dispatch(state: FigurePipelineState) -> Any:
                return [
                    Send(
                        "figure_agent",
                        {
                            "document_id": document_id,
                            "user_id": user_id,
                            "blackboard": state["blackboard"],
                            "target_ref": ref,
                            "force": force,
                        },
                    )
                    for ref in state["targets"]
                ] or "compound_resolve"

            async def figure(state: FigurePipelineState) -> dict[str, Any]:
                report = await coordinator.one(state["target_ref"], state["blackboard"], user_id, force=force)
                return {"reports": [report.model_dump()]}

            async def resolve(state: FigurePipelineState) -> dict[str, Any]:
                bundle = await self.services.skills()
                ctx = await bundle.context_for(
                    ToolContext(
                        user_id=user_id, document_id=document_id, run_id=new_id("run"), trace_id=new_id("tr")
                    ),
                    document_id,
                )
                result = await bundle.runtime.run("compound_resolve", document_id, {}, ctx, force=True)
                compounds = (
                    (result.artifacts[0].payload or {}).get("compounds", []) if result.artifacts else []
                )
                return {
                    "blackboard_v2": build_v2(state["blackboard"], compounds),
                    "questions": questions(
                        [FigureReport.model_validate(r) for r in state.get("reports", [])]
                    ),
                }

            def reconcile(state: FigurePipelineState) -> Any:
                return [
                    Send(
                        "query",
                        {
                            "target_ref": ref,
                            "blackboard_v2": state["blackboard_v2"],
                            "questions": {ref: items},
                        },
                    )
                    for ref, items in state["questions"].items()
                ] or "report"

            async def query(state: FigurePipelineState) -> dict[str, Any]:
                report = await coordinator.one(
                    state["target_ref"],
                    state["blackboard_v2"],
                    user_id,
                    mode="query",
                    query=state["questions"][state["target_ref"]],
                )
                return {"queries": [report.model_dump()]}

            async def report(state: FigurePipelineState) -> dict[str, Any]:
                reports = state.get("reports", [])
                queries = state.get("queries", [])
                result = {
                    "result": {
                        "document_id": document_id,
                        "status": "completed"
                        if all(r["status"] != "failed" for r in reports + queries)
                        else "failed",
                        "reports": reports,
                        "blackboard_v1": state["blackboard"],
                        "blackboard_v2": state["blackboard_v2"],
                        "reconcile": {"questions": state["questions"], "reports": queries},
                        "calls": sum(
                            FigureReport.model_validate(r).accounting.total for r in reports + queries
                        ),
                    }
                }
                from drugpaper.persistence.models import Document

                async with self.services.db.session(user_id) as session:
                    doc = await session.get(Document, document_id)
                    assert doc is not None and doc.user_id == user_id
                    doc.quality_report = {
                        **(doc.quality_report or {}),
                        "figure_agents": {
                            "status": result["result"]["status"],
                            "figure_count": len(reports),
                            "model_calls": result["result"]["calls"],
                            "needs_review": sum(r["status"] == "needs_review" for r in reports),
                            "timings_ms": {r["node_ref"]: r["accounting"]["timings_ms"] for r in reports},
                        },
                    }
                return result

            graph = StateGraph(FigurePipelineState)
            for name, fn in [
                ("build_blackboard", blackboard),
                ("figure_agent", figure),
                ("compound_resolve", resolve),
                ("query", query),
                ("report", report),
            ]:
                graph.add_node(name, fn)
            graph.add_edge(START, "build_blackboard")
            graph.add_conditional_edges("build_blackboard", dispatch)
            graph.add_edge("figure_agent", "compound_resolve")
            graph.add_conditional_edges("compound_resolve", reconcile)
            graph.add_edge("query", "report")
            graph.add_edge("report", END)
            compiled = graph.compile(checkpointer=saver)
            from drugpaper.docsrc.ir_source import IrDocumentSource

            src = await IrDocumentSource.load(self.services.db, document_id)
            from drugpaper.facts.metric_dictionary import MetricDictionary

            provenance = {
                "metric_dictionary": MetricDictionary().metrics,
                "version": __import__("drugpaper").__version__,
                "registry": {s.name: s.content_hash for s in await bundle.registry.list_visible(user_id)},
                "routing": self.services.settings.routing_config_path.read_text(),
                "models": {
                    name: bundle.models.router.resolve(name).model_dump(exclude={"api_key"})
                    for name in ("visual_classify", "figure_structure", "figure_self_check")
                },
            }
            stamp = hashlib.sha256(json.dumps(provenance, sort_keys=True, default=str).encode()).hexdigest()[
                :20
            ]
            config: RunnableConfig = {
                "configurable": {
                    "thread_id": f"{user_id}:figures:{document_id}:r{src.revision}:{stamp}"
                    + (":" + new_id("run") if force else "")
                },
                "recursion_limit": 50,
            }
            snapshot = await compiled.aget_state(config)
            if (
                snapshot.values
                and not snapshot.next
                and not force
                and snapshot.values["result"]["status"] != "failed"
            ):
                return cast(dict[str, Any], snapshot.values["result"])
            if snapshot.values and not snapshot.next:
                config["configurable"]["thread_id"] += ":retry:" + new_id("run")
            result = await compiled.ainvoke(
                None
                if snapshot.next
                else {
                    "document_id": document_id,
                    "user_id": user_id,
                    "force": force,
                    "reports": [],
                    "queries": [],
                },
                config,
            )
            return cast(dict[str, Any], result["result"])
