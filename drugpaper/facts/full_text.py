"""A user-scoped, revision-checked snapshot shared by lookup, reading and export."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast

from sqlalchemy import select

from drugpaper.domain.nodes import EvidenceRef, IRDocument
from drugpaper.facts.name_sources import is_current_name
from drugpaper.persistence.db import Database
from drugpaper.persistence.models import (
    ArtifactRow,
    AssayRow,
    CompoundRow,
    Document,
    FigureReportRow,
    MeasurementRow,
    StructureCandidateRow,
)
from drugpaper.persistence.repos.nodes import load_ir


def digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, default=str).encode()
    ).hexdigest()


def component_stamp() -> str:
    root = Path(__file__).resolve().parents[2]
    files = [
        *(p for p in (root / "drugpaper").rglob("*") if p.suffix in {".py", ".md", ".yaml", ".json"}),
        *(root / "prompts/skills").rglob("*.md"),
        *(root / "config").glob("*.yaml"),
    ]
    return digest(
        {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(set(files))}
    )


def valid_evidence(ir: IRDocument, evidence: EvidenceRef | dict[str, Any]) -> bool:
    e = evidence if isinstance(evidence, EvidenceRef) else EvidenceRef.model_validate(evidence)
    node = next((n for n in ir.nodes if n.ref == e.node_id and n.status != "removed"), None)
    if node is None:
        return False
    return e.char_span is None or 0 <= e.char_span[0] < e.char_span[1] <= len(node.text_raw or "")


@dataclass
class FullTextContext:
    user_id: str
    ir: IRDocument
    component_version: str = field(default_factory=component_stamp)
    compounds: list[dict[str, Any]] = field(default_factory=list)
    structures: list[dict[str, Any]] = field(default_factory=list)
    measurements: list[dict[str, Any]] = field(default_factory=list)
    assays: list[dict[str, Any]] = field(default_factory=list)
    reports: dict[str, dict[str, Any]] = field(default_factory=dict)
    extractions: dict[str, dict[str, Any]] = field(default_factory=dict)
    artifacts: list[dict[str, Any]] = field(default_factory=list)

    @property
    def fingerprint(self) -> str:
        return digest(
            {
                "version": "p6.1",
                "components": self.component_version,
                "ir": self.ir.model_dump(mode="json"),
                "compounds": self.compounds,
                "structures": self.structures,
                "measurements": self.measurements,
                "assays": self.assays,
                "reports": self.reports,
                "extractions": self.extractions,
                "artifacts": self.artifacts,
            }
        )

    def by_id(self) -> dict[str, dict[str, Any]]:
        return {c["compound_id"]: c for c in self.compounds}


async def load_context(db: Database, document_id: str, *, user_id: str = "default") -> FullTextContext:
    async with db.session(user_id) as session:
        doc = await session.get(Document, document_id)
        if doc is None or doc.user_id != user_id or doc.deleted_at is not None or doc.active_revision is None:
            raise PermissionError("document unavailable")
        ir = await load_ir(session, document_id, doc.active_revision)
        ctx = FullTextContext(user_id=user_id, ir=ir)
        nodes = {n.ref: n for n in ir.nodes if n.status != "removed"}
        artifacts = list(
            (
                await session.execute(
                    select(ArtifactRow)
                    .where(
                        ArtifactRow.user_id == user_id,
                        ArtifactRow.document_id == document_id,
                        ArtifactRow.is_active.is_(True),
                        ArtifactRow.status.in_(["completed", "validated", "needs_review", "human_corrected"]),
                    )
                    .order_by(ArtifactRow.id)
                )
            ).scalars()
        )
        current = {}
        for a in artifacts:
            node = nodes.get(a.target_ref)
            if a.target_ref != document_id and node is None:
                continue
            payload = a.payload or {}
            if a.artifact_type in {"text_extraction", "name_to_structure"} and (
                node is None
                or payload.get("source_text_sha256")
                != hashlib.sha256((node.text_raw or "").encode()).hexdigest()
            ):
                continue
            current[a.id] = a
            ctx.artifacts.append(
                {
                    "id": a.id,
                    "type": a.artifact_type,
                    "version": a.version,
                    "status": a.status,
                    "fingerprint": a.input_fingerprint,
                    "target_ref": a.target_ref,
                    "payload_hash": digest(payload),
                }
            )
            if a.artifact_type == "text_extraction":
                ctx.extractions[a.target_ref] = payload
        compounds = (
            await session.execute(
                select(CompoundRow)
                .where(CompoundRow.user_id == user_id, CompoundRow.document_id == document_id)
                .order_by(CompoundRow.primary_label)
            )
        ).scalars()
        for row in compounds:
            data = dict(row.payload)
            data["compound_id"] = row.id
            data["primary_label"] = row.primary_label
            data["evidence"] = [e for e in data.get("evidence", []) if valid_evidence(ir, e)]
            candidate = (
                await session.get(StructureCandidateRow, row.accepted_structure_id)
                if row.accepted_structure_id
                else None
            )
            if (
                candidate is not None
                and candidate.user_id == user_id
                and candidate.document_id == document_id
                and candidate.is_active
                and candidate.artifact_id in current
                and candidate.node_ref in nodes
                and await is_current_name(session, doc, candidate)
            ):
                data["accepted_structure"] = candidate.payload
            else:
                data["accepted_structure"] = None
            ctx.compounds.append(data)
        candidates = (
            await session.execute(
                select(StructureCandidateRow)
                .where(
                    StructureCandidateRow.user_id == user_id,
                    StructureCandidateRow.document_id == document_id,
                    StructureCandidateRow.is_active.is_(True),
                )
                .order_by(StructureCandidateRow.id)
            )
        ).scalars()
        for candidate in candidates:
            if (
                candidate.node_ref in nodes
                and candidate.artifact_id in current
                and await is_current_name(session, doc, candidate)
            ):
                ctx.structures.append(candidate.payload)
        for cls, destination in ((MeasurementRow, ctx.measurements), (AssayRow, ctx.assays)):
            rows = (
                await session.execute(
                    select(cls)
                    .where(cls.user_id == user_id, cls.document_id == document_id, cls.is_active.is_(True))
                    .order_by(cls.id)
                )
            ).scalars()
            for fact_row in cast(list[MeasurementRow | AssayRow], list(rows)):
                evidence = fact_row.payload.get("evidence", [])
                if (
                    fact_row.artifact_id in current
                    and evidence
                    and all(valid_evidence(ir, e) for e in evidence)
                ):
                    destination.append(fact_row.payload)
        reports = (
            await session.execute(
                select(FigureReportRow)
                .where(
                    FigureReportRow.user_id == user_id,
                    FigureReportRow.document_id == document_id,
                    FigureReportRow.mode == "full",
                )
                .order_by(FigureReportRow.created_at, FigureReportRow.id)
            )
        ).scalars()
        for report_row in reports:
            if report_row.node_ref in nodes:
                ctx.reports[report_row.node_ref] = report_row.payload
    return ctx
