from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import BaseModel, Field
from sqlalchemy import select

from drugpaper.facts.name_sources import is_current_name
from drugpaper.persistence.models import CompoundRow, Document, LabelClaimRow, StructureCandidateRow
from drugpaper.tools.protocol import Tool, ToolContext, ToolResult, tool

if TYPE_CHECKING:
    from drugpaper.services import Services


class CompoundsIn(BaseModel):
    query: str = ""
    limit: int = Field(default=20, ge=1, le=100)


class ReferencesIn(BaseModel):
    references: list[str] = Field(min_length=1, max_length=100)


def make_compound_tools(services: Services) -> list[Tool]:
    @tool("list_compounds", "查询当前文档的化合物编号、别名和已采纳结构引用；用于知识问答。")
    async def compounds(ctx: ToolContext, args: CompoundsIn) -> ToolResult:
        async with services.db.session(ctx.user_id) as session:
            document = await session.get(Document, ctx.document_id)
            if document is None or document.user_id != ctx.user_id:
                return ToolResult(ok=False, error="E_FORBIDDEN")
            rows = (
                await session.execute(
                    select(CompoundRow)
                    .where(CompoundRow.document_id == ctx.document_id, CompoundRow.user_id == ctx.user_id)
                    .order_by(CompoundRow.primary_label)
                )
            ).scalars()
            matched = [
                row.payload
                for row in rows
                if args.query.lower()
                in " ".join([row.primary_label, *row.payload.get("aliases", [])]).lower()
            ]
            results = []
            refs: list[str] = []
            for compound in matched[: args.limit]:
                data = dict(compound)
                if compound.get("accepted_structure_id"):
                    candidate = await session.get(StructureCandidateRow, compound["accepted_structure_id"])
                    if (
                        candidate is not None
                        and candidate.user_id == ctx.user_id
                        and candidate.is_active
                        and await is_current_name(session, document, candidate)
                    ):
                        data["accepted_structure"] = candidate.payload
                        refs.extend(e["node_id"] for e in candidate.payload["evidence"])
                results.append(data)
            return ToolResult(
                ok=True, data={"compounds": results, "total": len(matched)}, refs=sorted(set(refs))
            )

    @tool(
        "mol.lookup_references", "从当前用户文档的显式化合物编号查找唯一有效完整结构；R/S需有唯一手性中心。"
    )
    async def lookup(ctx: ToolContext, args: ReferencesIn) -> ToolResult:
        from drugpaper.facts.reference_delivery import resolve_references

        async with services.db.session(ctx.user_id) as session:
            document = await session.get(Document, ctx.document_id)
            if document is None or document.user_id != ctx.user_id:
                return ToolResult(ok=False, error="E_FORBIDDEN")
            claims = (
                await session.execute(
                    select(LabelClaimRow).where(
                        LabelClaimRow.document_id == ctx.document_id,
                        LabelClaimRow.user_id == ctx.user_id,
                        LabelClaimRow.is_active.is_(True),
                    )
                )
            ).scalars()
            structures = (
                await session.execute(
                    select(StructureCandidateRow).where(
                        StructureCandidateRow.document_id == ctx.document_id,
                        StructureCandidateRow.user_id == ctx.user_id,
                        StructureCandidateRow.is_active.is_(True),
                    )
                )
            ).scalars()
            current_structures = [
                s.payload for s in structures if await is_current_name(session, document, s)
            ]
            return ToolResult(
                ok=True,
                data=resolve_references(args.references, [c.payload for c in claims], current_structures),
            )

    return [compounds, lookup]
