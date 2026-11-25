"""P6 正文、实验名称、化合物台账与页包；全文理解尚未完成。"""

from __future__ import annotations

from typing import Any

from drugpaper.agents.reader.page_pack import load_page_pack
from drugpaper.domain.ids import new_id
from drugpaper.facts.text_extraction import EXTRACT_ROLES
from drugpaper.persistence.models import Document
from drugpaper.persistence.repos.nodes import load_ir
from drugpaper.services import Services
from drugpaper.tools.protocol import ToolContext


class TextPipeline:
    def __init__(self, services: Services) -> None:
        self.services = services

    async def run(self, document_id: str, *, user_id: str = "default", force: bool = False) -> dict[str, Any]:
        async with self.services.db.session(user_id) as session:
            document = await session.get(Document, document_id)
            if document is None or document.user_id != user_id:
                raise PermissionError("document unavailable")
            ir = await load_ir(session, document_id, document.active_revision)
        bundle = await self.services.skills()
        runs: list[dict[str, Any]] = []
        name_runs: list[dict[str, Any]] = []
        run_id, trace_id = new_id("run"), new_id("tr")
        for node in sorted(ir.nodes, key=lambda n: (n.reading_order, n.local_id)):
            if (
                node.node_type not in {"paragraph", "list"}
                or node.status == "removed"
                or node.section_role not in EXTRACT_ROLES | {"introduction"}
            ):
                continue
            ctx = await bundle.context_for(
                ToolContext(user_id=user_id, document_id=document_id, run_id=run_id, trace_id=trace_id),
                node.ref,
            )
            outcome = await bundle.runtime.run("text_extract", node.ref, {}, ctx, force=force)
            runs.append(
                {
                    "node_ref": node.ref,
                    "run_id": outcome.run_id,
                    "status": outcome.status,
                    "cached": outcome.cached,
                    "error": outcome.error,
                    "artifact_ids": [a.artifact_id for a in outcome.artifacts],
                }
            )
            if node.section_role == "experimental":
                # Reload materials after T1; the OPSIN Skill independently anchors the title.
                name_ctx = await bundle.context_for(
                    ToolContext(user_id=user_id, document_id=document_id, run_id=run_id, trace_id=trace_id),
                    node.ref,
                )
                named = await bundle.runtime.run("name_to_structure", node.ref, {}, name_ctx, force=force)
                name_runs.append(
                    {
                        "node_ref": node.ref,
                        "run_id": named.run_id,
                        "status": named.status,
                        "cached": named.cached,
                        "error": named.error,
                        "artifacts": [a.model_dump(mode="json") for a in named.artifacts],
                    }
                )
        ledger_ctx = await bundle.context_for(
            ToolContext(user_id=user_id, document_id=document_id, run_id=run_id, trace_id=trace_id),
            document_id,
            include_document_image=False,
        )
        resolved = await bundle.runtime.run("compound_resolve", document_id, {}, ledger_ctx, force=force)
        from drugpaper.facts.text_measurements import project_text_measurements

        measurements = await project_text_measurements(self.services.db, document_id, user_id=user_id)
        packs = [
            (await load_page_pack(self.services.db, document_id, page.page, user_id=user_id)).model_dump(
                mode="json"
            )
            for page in ir.pages
        ]
        return {
            "document_id": document_id,
            "revision": ir.revision,
            "stage": "p6-text-and-compounds",
            "status": "failed"
            if any(r["status"] == "failed" for r in runs + name_runs) or resolved.status == "failed"
            else "ready"
            if all(p["ready"] and not p["warnings"] for p in packs)
            and all(a.status == "completed" for a in resolved.artifacts)
            and all(a["status"] == "completed" for r in name_runs for a in r["artifacts"])
            else "needs_review",
            "runs": runs,
            "name_runs": name_runs,
            "compound_resolution": {
                "status": resolved.status,
                "run_id": resolved.run_id,
                "error": resolved.error,
                "artifacts": [a.model_dump(mode="json") for a in resolved.artifacts],
            },
            "p6_complete": False,
            "text_measurements": measurements,
            "page_packs": packs,
        }
