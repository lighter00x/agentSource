"""Read current T1 mentions without making stale or unverified artifacts authoritative."""

from __future__ import annotations

import hashlib
from typing import Any

from drugpaper.domain.nodes import IRDocument
from drugpaper.facts.text_extraction import EXTRACT_ROLES
from drugpaper.persistence.models import ArtifactRow


def text_mentions(ir: IRDocument, artifacts: list[ArtifactRow]) -> list[dict[str, Any]]:
    nodes = {n.ref: n for n in ir.nodes if n.status != "removed"}
    mentions = []
    for artifact in artifacts:
        if artifact.artifact_type != "text_extraction" or artifact.status not in {
            "completed",
            "needs_review",
        }:
            continue
        node = nodes.get(artifact.target_ref)
        payload = artifact.payload or {}
        if (
            node is None
            or node.section_role not in EXTRACT_ROLES | {"introduction"}
            or payload.get("source_text_sha256") != hashlib.sha256((node.text_raw or "").encode()).hexdigest()
        ):
            continue
        for item in payload.get("compound_mentions", []):
            mentions.append(
                {
                    **item,
                    "section_role": node.section_role,
                    "source_status": artifact.status,
                    "artifact_id": artifact.id,
                    "node_ref": node.ref,
                    "text_raw": node.text_raw or "",
                }
            )
    return mentions
