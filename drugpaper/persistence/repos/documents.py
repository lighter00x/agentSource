"""文档与解析版本。"""

from __future__ import annotations

from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from drugpaper.domain.ids import new_document_id
from drugpaper.persistence.models import Document, ParseRevision, utcnow

STATUSES = ("uploaded", "parsing", "parsed", "ir_built", "ready", "failed")


async def create_document(
    s: AsyncSession,
    *,
    sha256: str,
    filename: str | None = None,
    user_id: str = "default",
    document_id: str | None = None,
) -> tuple[Document, bool]:
    """返回 (文档, 是否新建)。同一用户上传相同内容时返回已有文档（用户内去重）。"""
    existing = (
        (await s.execute(select(Document).where(Document.user_id == user_id, Document.sha256 == sha256)))
        .scalars()
        .first()
    )
    if existing is not None:
        return existing, False
    doc = Document(id=document_id or new_document_id(), user_id=user_id, sha256=sha256, filename=filename)
    s.add(doc)
    await s.flush()
    return doc, True


async def get_document(s: AsyncSession, document_id: str) -> Document | None:
    doc = await s.get(Document, document_id)
    return doc if doc is not None and doc.deleted_at is None else None


async def list_documents(s: AsyncSession, *, status: str | None = None, limit: int = 100) -> list[Document]:
    stmt = select(Document).where(Document.deleted_at.is_(None))
    if status:
        stmt = stmt.where(Document.status == status)
    return list((await s.execute(stmt.order_by(Document.created_at.desc()).limit(limit))).scalars())


async def set_status(
    s: AsyncSession, document_id: str, status: str, *, error: dict[str, Any] | None = None
) -> None:
    if status not in STATUSES:
        raise ValueError(f"未知状态：{status}")
    doc = await s.get(Document, document_id)
    if doc is None:
        raise KeyError(document_id)
    doc.status = status
    doc.error = error
    doc.updated_at = utcnow()


async def next_revision(s: AsyncSession, document_id: str) -> int:
    cur = (
        await s.execute(
            select(func.max(ParseRevision.revision)).where(ParseRevision.document_id == document_id)
        )
    ).scalar()
    return int(cur or 0) + 1


async def create_revision(
    s: AsyncSession,
    document_id: str,
    *,
    parser: str,
    parser_version: str,
    raw_blob_refs: dict[str, Any],
    user_id: str = "default",
) -> ParseRevision:
    rev = ParseRevision(
        document_id=document_id,
        revision=await next_revision(s, document_id),
        user_id=user_id,
        parser=parser,
        parser_version=parser_version,
        raw_blob_refs=raw_blob_refs,
        status="created",
    )
    s.add(rev)
    await s.flush()
    return rev


async def get_revision(
    s: AsyncSession, document_id: str, revision: int | None = None
) -> ParseRevision | None:
    stmt = select(ParseRevision).where(ParseRevision.document_id == document_id)
    if revision is not None:
        stmt = stmt.where(ParseRevision.revision == revision)
    return (await s.execute(stmt.order_by(ParseRevision.revision.desc()))).scalars().first()
