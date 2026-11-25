from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import BaseModel, Field
from sqlalchemy import select

from drugpaper.persistence.models import Document, FigureReportRow, MeasurementRow
from drugpaper.tools.protocol import Tool, ToolContext, ToolResult, tool

if TYPE_CHECKING:
    from drugpaper.services import Services


class ReportInput(BaseModel):
    node_ref: str


class FactInput(BaseModel):
    metric: str | None = None
    subject_label: str | None = None
    limit: int = Field(default=30, ge=1, le=100)


def make_figure_fact_tools(services: Services) -> list[Tool]:
    async def guard(ctx: ToolContext) -> None:
        async with services.db.session(ctx.user_id) as session:
            doc = await session.get(Document, ctx.document_id)
            if doc is None or doc.user_id != ctx.user_id:
                raise PermissionError("document unavailable")

    @tool("get_figure_report", "Read a submitted figure report without waiting; pending when not submitted.")
    async def report(ctx: ToolContext, args: ReportInput) -> ToolResult:
        from drugpaper.domain.ids import parse_ref

        await guard(ctx)
        parsed = parse_ref(args.node_ref)
        if parsed is None or parsed[0] != ctx.document_id:
            raise PermissionError("figure outside document")
        async with services.db.session(ctx.user_id) as session:
            row = (
                await session.execute(
                    select(FigureReportRow)
                    .where(
                        FigureReportRow.user_id == ctx.user_id,
                        FigureReportRow.node_ref == args.node_ref,
                        FigureReportRow.mode == "full",
                    )
                    .order_by(FigureReportRow.created_at.desc())
                    .limit(1)
                )
            ).scalar_one_or_none()
            return ToolResult(
                ok=True,
                data={"status": "pending"} if row is None else {"report": row.payload},
                refs=[args.node_ref] if row else [],
            )

    @tool("query_measurements", "Query evidence-backed table/chart measurements in the current document.")
    async def measurements(ctx: ToolContext, args: FactInput) -> ToolResult:
        await guard(ctx)
        async with services.db.session(ctx.user_id) as session:
            rows = (
                await session.execute(
                    select(MeasurementRow).where(
                        MeasurementRow.document_id == ctx.document_id,
                        MeasurementRow.user_id == ctx.user_id,
                        MeasurementRow.is_active.is_(True),
                    )
                )
            ).scalars()
            values = [
                r.payload
                for r in rows
                if (args.metric is None or r.payload.get("metric") == args.metric)
                and (args.subject_label is None or r.payload.get("subject_label") == args.subject_label)
            ][: args.limit]
            return ToolResult(
                ok=True,
                data={"measurements": values},
                refs=list(dict.fromkeys(e["node_id"] for value in values for e in value["evidence"])),
            )

    return [report, measurements]
