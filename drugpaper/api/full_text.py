from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Response
from pydantic import BaseModel, Field
from sqlalchemy import select

from drugpaper.agents.lookup.batch import DocLookup
from drugpaper.agents.lookup.models import LookupBatchResult, LookupQuestion
from drugpaper.agents.reader.graph import ReaderAgent, ReaderProgress
from drugpaper.api.deps import ServicesDep
from drugpaper.api.errors import ApiError, not_found
from drugpaper.exporters.package import create_export
from drugpaper.facts.full_text import load_context
from drugpaper.orchestration.full_text_pipeline import FullTextPipeline
from drugpaper.persistence.models import ExportRow, FullTextResultRow

router = APIRouter(tags=["full-text"])


class LookupRequest(BaseModel):
    questions: list[LookupQuestion] = Field(min_length=1, max_length=8)


class ReadRequest(BaseModel):
    from_page: int | None = Field(default=None, ge=0)


class UnderstandRequest(ReadRequest):
    prepare_text: bool = True
    force: bool = False


class ExportRequest(BaseModel):
    result_id: str | None = None


async def guard(sv: ServicesDep, document_id: str) -> None:
    try:
        await load_context(sv.db, document_id)
    except PermissionError as exc:
        raise not_found("文档", document_id) from exc


@router.post("/documents/{document_id}/lookups", response_model=LookupBatchResult)
async def lookup(document_id: str, body: LookupRequest, sv: ServicesDep) -> LookupBatchResult:
    await guard(sv, document_id)
    ctx = await load_context(sv.db, document_id)
    return await DocLookup(sv).lookup(body.questions, ctx)


@router.post("/documents/{document_id}/reader", response_model=ReaderProgress)
async def read(document_id: str, body: ReadRequest, sv: ServicesDep) -> ReaderProgress:
    await guard(sv, document_id)
    try:
        return await ReaderAgent(sv).read(document_id, from_page=body.from_page)
    except ValueError as exc:
        raise ApiError(409, "READER_CHECKPOINT", str(exc)) from exc


@router.post("/documents/{document_id}/reader/reread", response_model=ReaderProgress)
async def reread(document_id: str, body: ReadRequest, sv: ServicesDep) -> ReaderProgress:
    if body.from_page is None:
        raise ApiError(422, "FROM_PAGE_REQUIRED", "重读必须指定from_page")
    return await read(document_id, body, sv)


@router.post("/documents/{document_id}/full-text")
async def understand(document_id: str, body: UnderstandRequest, sv: ServicesDep) -> dict[str, Any]:
    await guard(sv, document_id)
    return await FullTextPipeline(sv).run(
        document_id, prepare_text=body.prepare_text, force=body.force, from_page=body.from_page
    )


@router.get("/documents/{document_id}/full-text")
async def result(document_id: str, sv: ServicesDep) -> dict[str, Any]:
    await guard(sv, document_id)
    ctx = await load_context(sv.db, document_id)
    async with sv.db.session() as session:
        row = (
            await session.execute(
                select(FullTextResultRow)
                .where(
                    FullTextResultRow.document_id == document_id,
                    FullTextResultRow.user_id == "default",
                    FullTextResultRow.revision == ctx.ir.revision,
                    FullTextResultRow.fingerprint == ctx.fingerprint,
                )
                .order_by(FullTextResultRow.created_at.desc(), FullTextResultRow.id.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        if row is None:
            raise not_found("当前全文结果", document_id)
        return {"result_id": row.id, "status": row.status, **row.payload}


@router.post("/documents/{document_id}/exports", status_code=201)
async def export(document_id: str, body: ExportRequest, sv: ServicesDep) -> dict[str, Any]:
    await guard(sv, document_id)
    try:
        return await create_export(sv, document_id, result_id=body.result_id)
    except ValueError as exc:
        raise ApiError(409, "EXPORT_NOT_READY", str(exc)) from exc


@router.get("/exports/{export_id}/download")
async def download(export_id: str, sv: ServicesDep) -> Response:
    async with sv.db.session() as session:
        row = await session.get(ExportRow, export_id)
        if row is None or row.user_id != "default":
            raise not_found("导出", export_id)
        document_id, blob = row.document_id, row.blob_ref
    await guard(sv, document_id)
    data = await sv.store_for("default").get(blob)
    return Response(
        data,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{export_id}.zip"'},
    )
