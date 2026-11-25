from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from sqlalchemy import select

from drugpaper.api.figure_results import result_response
from drugpaper.facts.figure_result import persist_result
from drugpaper.persistence.models import AssayRow, Document, FigureReportRow, MeasurementRow

router = APIRouter()


async def visible(request: Request, document_id: str) -> None:
    async with request.app.state.services.db.session("default") as session:
        doc = await session.get(Document, document_id)
        if doc is None or doc.user_id != "default":
            raise HTTPException(404, "document not found")


@router.get("/documents/{document_id}/figure-reports")
async def reports(request: Request, document_id: str) -> list[dict[str, Any]]:
    await visible(request, document_id)
    async with request.app.state.services.db.session("default") as session:
        rows = (
            await session.execute(
                select(FigureReportRow)
                .where(FigureReportRow.document_id == document_id, FigureReportRow.user_id == "default")
                .order_by(FigureReportRow.created_at.desc())
            )
        ).scalars()
        result = []
        for row in rows:
            if row.payload.get("backfilled_extraction") and not row.payload.get("result_file"):
                metadata, _ = await persist_result(
                    request.app.state.services.store_for("default"), row.payload
                )
                row.payload = {**row.payload, "result_file": metadata}
            result.append(row.payload)
        return result


@router.get("/documents/{document_id}/figure-reports/{report_id}/result")
async def report_result(
    request: Request,
    document_id: str,
    report_id: str,
    download: bool = False,
    view: Literal["summary", "delivery", "full"] = "summary",
) -> JSONResponse:
    await visible(request, document_id)
    sv = request.app.state.services
    async with sv.db.session("default") as session:
        row = await session.get(FigureReportRow, report_id)
        if row is None or row.document_id != document_id or row.user_id != "default":
            raise HTTPException(404, "图表报告不存在")
        if not row.payload.get("backfilled_extraction"):
            raise HTTPException(409, "此报告尚无回填结果，不能作为最终结果导出")
        metadata, result = await persist_result(sv.store_for("default"), row.payload)
        if row.payload.get("result_file") != metadata:
            row.payload = {**row.payload, "result_file": metadata}
    return result_response(result, download, view)


@router.get("/documents/{document_id}/measurements")
async def measurements(request: Request, document_id: str) -> list[dict[str, Any]]:
    await visible(request, document_id)
    async with request.app.state.services.db.session("default") as session:
        rows = (
            await session.execute(
                select(MeasurementRow).where(
                    MeasurementRow.document_id == document_id,
                    MeasurementRow.user_id == "default",
                    MeasurementRow.is_active.is_(True),
                )
            )
        ).scalars()
        return [row.payload for row in rows]


@router.get("/documents/{document_id}/assays")
async def assays(request: Request, document_id: str) -> list[dict[str, Any]]:
    await visible(request, document_id)
    async with request.app.state.services.db.session("default") as session:
        rows = (
            await session.execute(
                select(AssayRow).where(
                    AssayRow.document_id == document_id,
                    AssayRow.user_id == "default",
                    AssayRow.is_active.is_(True),
                )
            )
        ).scalars()
        return [row.payload for row in rows]
