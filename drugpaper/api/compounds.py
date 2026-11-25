"""P4 台账只读接口；请求身份目前沿用 default，P7 统一接入。"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query
from sqlalchemy import select

from drugpaper.api.deps import SessionDep, require_document
from drugpaper.api.errors import not_found
from drugpaper.persistence.models import ChemistryConflictRow, CompoundRow, StructureCandidateRow

router = APIRouter(tags=["chemistry"])


@router.get("/documents/{document_id}/compounds")
async def list_compounds(
    document_id: str, session: SessionDep, offset: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=200)
) -> list[dict[str, Any]]:
    doc = await require_document(session, document_id)
    if doc.user_id != "default":
        raise not_found("文档", document_id)
    rows = (
        await session.execute(
            select(CompoundRow)
            .where(CompoundRow.document_id == document_id, CompoundRow.user_id == "default")
            .order_by(CompoundRow.primary_label)
            .offset(offset)
            .limit(limit)
        )
    ).scalars()
    return [row.payload for row in rows]


@router.get("/documents/{document_id}/compounds/{compound_id}")
async def compound_detail(document_id: str, compound_id: str, session: SessionDep) -> dict[str, Any]:
    doc = await require_document(session, document_id)
    compound = await session.get(CompoundRow, compound_id)
    if (
        doc.user_id != "default"
        or compound is None
        or compound.user_id != "default"
        or compound.document_id != document_id
    ):
        raise not_found("化合物", compound_id)
    structures = (
        await session.execute(
            select(StructureCandidateRow).where(
                StructureCandidateRow.compound_id == compound_id,
                StructureCandidateRow.user_id == "default",
                StructureCandidateRow.is_active.is_(True),
            )
        )
    ).scalars()
    conflicts = (
        await session.execute(
            select(ChemistryConflictRow).where(
                ChemistryConflictRow.compound_id == compound_id,
                ChemistryConflictRow.user_id == "default",
                ChemistryConflictRow.is_active.is_(True),
            )
        )
    ).scalars()
    return {
        "compound": compound.payload,
        "structures": [s.payload for s in structures],
        "conflicts": [c.payload for c in conflicts],
    }
