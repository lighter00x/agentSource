from __future__ import annotations

import asyncio
from typing import Any

from drugpaper.docsrc.ir_source import IrDocumentSource
from drugpaper.domain.ids import new_id
from drugpaper.services import Services
from drugpaper.tools.protocol import ToolContext


class MoleculePipeline:
    """P4 编排只调用 SkillRuntime；每个分子独立 Work Unit，可单独重跑。"""

    def __init__(self, services: Services) -> None:
        self.services = services

    async def run(
        self,
        document_id: str,
        *,
        user_id: str = "default",
        force: bool = False,
        targets: list[str] | None = None,
    ) -> dict[str, Any]:
        bundle = await self.services.skills()
        source = await IrDocumentSource.load(self.services.db, document_id)
        trace = new_id("tr")
        tool_ctx = ToolContext(user_id=user_id, document_id=document_id, run_id=new_id("run"), trace_id=trace)
        results: list[dict[str, Any]] = []
        gate = asyncio.Semaphore(4)

        async def invoke(name: str, ref: str) -> None:
            async with gate:
                ctx = await bundle.context_for(tool_ctx, ref)
                result = await bundle.runtime.run(name, ref, {}, ctx, force=force)
                results.append(
                    {
                        "skill": name,
                        "target_ref": ref,
                        "status": result.status,
                        "error": result.error,
                        "artifact_ids": [a.artifact_id for a in result.artifacts],
                    }
                )
                if result.status != "completed":
                    raise RuntimeError(f"{name}: {result.error}")

        for node in source.ir.nodes:
            if (
                node.node_type not in {"scheme", "figure", "table"}
                or node.status == "removed"
                or not node.geometry.regions
                or targets is not None
                and node.ref not in targets
            ):
                continue
            await invoke("molecule_detect", node.ref)
            current = await IrDocumentSource.load(self.services.db, document_id)
            molecules = [
                n
                for n in current.ir.nodes
                if n.parent_id == node.local_id and n.node_type == "molecule" and n.status != "removed"
            ]
            await asyncio.gather(*(invoke("molecule_recognize", n.ref) for n in molecules))
            await invoke("som_render", node.ref)
            await invoke("label_bind", node.ref)
        await invoke("compound_resolve", document_id)
        return {"document_id": document_id, "trace_id": trace, "runs": results}
