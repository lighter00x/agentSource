"""API 依赖：Services、会话、文档与节点解析。"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from drugpaper.api.errors import ApiError, not_found
from drugpaper.domain import ids
from drugpaper.persistence.models import Asset, Document
from drugpaper.persistence.repos.documents import get_document
from drugpaper.services import Services


def get_services(request: Request) -> Services:
    sv: Services = request.app.state.services
    return sv


ServicesDep = Annotated[Services, Depends(get_services)]


async def get_session(sv: ServicesDep) -> AsyncIterator[AsyncSession]:
    async with sv.db.session() as s:  # P7：从请求身份取 user_id
        yield s


SessionDep = Annotated[AsyncSession, Depends(get_session)]


async def require_document(s: AsyncSession, document_id: str) -> Document:
    doc = await get_document(s, document_id)
    if doc is None:
        raise not_found("文档", document_id)
    return doc


async def require_ir(s: AsyncSession, document_id: str) -> Document:
    doc = await require_document(s, document_id)
    if doc.active_revision is None:
        raise ApiError(409, "NOT_READY", f"文档尚未完成解析（status={doc.status}）", status=doc.status)
    return doc


def split_ref(node_ref: str) -> tuple[str, int, str]:
    parsed = ids.parse_ref(node_ref)
    if parsed is None:
        raise ApiError(
            400, "BAD_NODE_REF", f"节点引用格式应为 {{document_id}}/r{{rev}}/{{local_id}}：{node_ref}"
        )
    return parsed


async def pdf_ref_of(s: AsyncSession, document_id: str) -> str:
    rows = (
        await s.execute(
            select(Asset).where(Asset.document_id == document_id, Asset.kind.in_(("origin_pdf", "pdf")))
        )
    ).scalars()
    by_kind = {a.kind: a.blob_ref for a in rows}
    ref = by_kind.get("origin_pdf") or by_kind.get("pdf")
    if ref is None:
        raise ApiError(409, "PDF_MISSING", "文档没有 PDF 资产")
    return ref
