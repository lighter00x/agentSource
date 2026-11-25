"""OPSIN structures are current only while the experimental source still matches."""

from __future__ import annotations

import hashlib

from sqlalchemy.ext.asyncio import AsyncSession

from drugpaper.domain.ids import parse_ref
from drugpaper.persistence.models import ArtifactRow, Document, NodeRow, StructureCandidateRow


async def is_current_name(
    session: AsyncSession, document: Document, candidate: StructureCandidateRow
) -> bool:
    if candidate.source != "opsin":
        return True
    parsed = parse_ref(candidate.node_ref)
    if parsed is None or parsed[:2] != (document.id, document.active_revision):
        return False
    node = await session.get(NodeRow, parsed)
    artifact = await session.get(ArtifactRow, candidate.artifact_id)
    return bool(
        node is not None
        and node.user_id == document.user_id
        and node.status != "removed"
        and node.section_role == "experimental"
        and artifact is not None
        and artifact.user_id == document.user_id
        and artifact.is_active
        and artifact.status in {"completed", "needs_review"}
        and (artifact.payload or {}).get("source_text_sha256")
        == hashlib.sha256((node.text_raw or "").encode()).hexdigest()
    )
