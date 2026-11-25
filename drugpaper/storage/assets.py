"""资产登记：把 blob 与文档关联起来（`assets` 表）。"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from drugpaper.domain.ids import new_id
from drugpaper.persistence.models import Asset


async def register_asset(
    session: AsyncSession,
    document_id: str,
    kind: str,
    ref: str,
    *,
    content_type: str | None = None,
    path: str | None = None,
    meta: dict[str, Any] | None = None,
    user_id: str = "default",
) -> Asset:
    """同一文档、同一 kind、同一 path 的资产已存在时直接返回（幂等）。"""
    stmt = select(Asset).where(Asset.document_id == document_id, Asset.kind == kind, Asset.blob_ref == ref)
    if path is not None:
        stmt = stmt.where(Asset.path == path)
    existing = (await session.execute(stmt)).scalars().first()
    if existing is not None:
        return existing
    asset = Asset(
        id=new_id("ast"),
        user_id=user_id,
        document_id=document_id,
        kind=kind,
        blob_ref=ref,
        sha256=ref,
        content_type=content_type,
        path=path,
        meta=meta or {},
    )
    session.add(asset)
    await session.flush()
    return asset


async def list_assets(session: AsyncSession, document_id: str, kind: str | None = None) -> list[Asset]:
    stmt = select(Asset).where(Asset.document_id == document_id)
    if kind is not None:
        stmt = stmt.where(Asset.kind == kind)
    return list((await session.execute(stmt.order_by(Asset.kind, Asset.path))).scalars())


async def asset_by_path(session: AsyncSession, document_id: str, path: str) -> Asset | None:
    stmt = select(Asset).where(Asset.document_id == document_id, Asset.path == path)
    return (await session.execute(stmt)).scalars().first()
