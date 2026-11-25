from __future__ import annotations

import asyncio
from typing import Any

from drugpaper.agents.coordinator.blackboard import build_v1, build_v2
from drugpaper.agents.coordinator.reconcile import questions
from drugpaper.agents.figure.graph import FigureAgent
from drugpaper.agents.figure.report import FigureReport
from drugpaper.docsrc.ir_source import IrDocumentSource
from drugpaper.domain.ids import new_id
from drugpaper.services import Services
from drugpaper.tools.protocol import ToolContext


class Coordinator:
    def __init__(self, services: Services, checkpointer: Any = None):
        self.services = services
        self.checkpointer = checkpointer
        self.gate = asyncio.Semaphore(services.settings.figure_concurrency)

    async def one(
        self,
        ref: str,
        blackboard: dict[str, Any],
        user_id: str = "default",
        mode: str = "full",
        query: list[str] | None = None,
        force: bool = False,
    ) -> FigureReport:
        async with self.gate:
            return await FigureAgent(self.services, self.checkpointer).run(
                ref, user_id=user_id, blackboard=blackboard, mode=mode, questions=query, force=force
            )

    async def run(self, document_id: str, *, user_id: str = "default", force: bool = False) -> dict[str, Any]:
        bundle = await self.services.skills()
        # No document bytes or facts enter another user's coordinator.
        await bundle.context_for(
            ToolContext(
                user_id=user_id, document_id=document_id, run_id=new_id("run"), trace_id=new_id("tr")
            ),
            document_id,
        )
        source = await IrDocumentSource.load(self.services.db, document_id)
        board = build_v1(source.ir)
        visuals = sorted(
            [
                n
                for n in source.ir.nodes
                if n.node_type in {"figure", "table", "scheme"}
                and n.status != "removed"
                and n.geometry.regions
            ],
            key=lambda n: (n.page, 0 if n.node_type == "scheme" else 1, n.local_id),
        )
        reports = await asyncio.gather(*(self.one(node.ref, board, user_id, force=force) for node in visuals))
        tool_ctx = ToolContext(
            user_id=user_id, document_id=document_id, run_id=new_id("run"), trace_id=new_id("tr")
        )
        ctx = await bundle.context_for(tool_ctx, document_id)
        resolved = await bundle.runtime.run("compound_resolve", document_id, {}, ctx, force=True)
        compounds = (resolved.artifacts[0].payload or {}).get("compounds", []) if resolved.artifacts else []
        board2 = build_v2(board, compounds)
        directed = questions(reports)
        queries = await asyncio.gather(
            *(self.one(ref, board2, user_id, mode="query", query=items) for ref, items in directed.items())
        )
        return {
            "document_id": document_id,
            "blackboard_v1": board,
            "blackboard_v2": board2,
            "reports": [r.model_dump() for r in reports],
            "reconcile": {"questions": directed, "reports": [r.model_dump() for r in queries]},
            "calls": sum(r.accounting.total for r in [*reports, *queries]),
            "timings_ms": {r.node_ref: r.accounting.timings_ms for r in reports},
            "status": "completed" if all(r.status != "failed" for r in reports) else "failed",
        }
