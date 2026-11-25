"""P6 页包基础：原文起始页归属、依赖就绪、显式复核及有界阅读单元。"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Literal

from pydantic import BaseModel, Field
from sqlalchemy import select

from drugpaper.capabilities.tokens import estimate_tokens
from drugpaper.domain.nodes import VISUAL_TYPES, IRDocument
from drugpaper.facts.text_extraction import EXTRACT_ROLES
from drugpaper.persistence.db import Database
from drugpaper.persistence.models import ArtifactRow, Document, FigureReportRow
from drugpaper.persistence.repos.nodes import load_ir


class ReadingUnit(BaseModel):
    items: list[dict[str, Any]] = Field(default_factory=list)
    estimated_tokens: int = 0


class PagePack(BaseModel):
    document_id: str
    revision: int
    page: int
    mode: Literal["read", "skim", "skip"] = "read"
    ready: bool = False
    pending_refs: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    section_map: dict[str, list[str]] = Field(default_factory=dict)
    units: list[ReadingUnit] = Field(default_factory=list)
    estimated_tokens: int = 0


def tokens(items: list[dict[str, Any]]) -> int:
    return estimate_tokens(json.dumps(items, ensure_ascii=False))


def build_page_pack(
    ir: IRDocument,
    page: int,
    *,
    reports: dict[str, dict[str, Any]] | None = None,
    extractions: dict[str, dict[str, Any]] | None = None,
    max_tokens: int = 7000,
) -> PagePack:
    if page not in {p.page for p in ir.pages}:
        raise ValueError("page outside document")
    if max_tokens < 512:
        raise ValueError("page pack budget must be at least 512")
    reports, extractions = reports or {}, extractions or {}
    pack = PagePack(document_id=ir.document_id, revision=ir.revision, page=page)
    nodes = sorted(
        (n for n in ir.nodes if n.status != "removed" and n.page == page),
        key=lambda n: (n.reading_order, n.local_id),
    )
    paragraphs = [n for n in nodes if n.node_type in {"paragraph", "list"}]
    roles = {n.section_role for n in paragraphs}
    if roles and roles <= {"references", "acknowledgments", "acknowledgements"}:
        pack.mode = "skip"
    elif roles and roles <= {"experimental"}:
        pack.mode = "skim"
    items: list[dict[str, Any]] = []
    by_id = ir.index()
    for node in nodes:
        section_path: list[str] = []
        parent = node
        visited: set[str] = set()
        while parent.local_id not in visited:
            visited.add(parent.local_id)
            if parent.node_type in {"section", "heading"}:
                section_path.insert(0, parent.label or parent.text_raw or parent.local_id)
            if not parent.parent_id or parent.parent_id not in by_id:
                break
            parent = by_id[parent.parent_id]
        pack.section_map[node.ref] = section_path or ([node.section_role] if node.section_role else [])
        if node.node_type in {"paragraph", "list", "footnote"}:
            raw = node.text_raw or ""
            # 使用 raw 偏移保持长段落可回跳；每个切片的实际 token 包含JSON开销。
            start = 0
            while start < len(raw):
                end = min(len(raw), start + max_tokens)
                item = {
                    "kind": "text",
                    "node_ref": node.ref,
                    "section_role": node.section_role,
                    "section_path": pack.section_map[node.ref],
                    "char_span": [start, end],
                    "text_raw": raw[start:end],
                }
                while tokens([item]) > max_tokens:
                    end = start + (end - start) // 2
                    if end <= start:
                        raise ValueError("text item cannot fit page pack")
                    item.update(char_span=[start, end], text_raw=raw[start:end])
                items.append(item)
                start = end
            if node.node_type in {"paragraph", "list"} and node.section_role in EXTRACT_ROLES | {
                "introduction"
            }:
                extraction = extractions.get(node.ref)
                if extraction is None or (
                    extraction.get("source_text_sha256")
                    and extraction["source_text_sha256"] != hashlib.sha256(raw.encode()).hexdigest()
                ):
                    pack.pending_refs.append(node.ref)
                elif extraction.get("status") in {"completed", "needs_review"}:
                    # 每个事实保留其证据；不把模型的聚合统计当作事实。
                    if extraction.get("status") == "needs_review":
                        pack.warnings.append(f"text needs_review: {node.ref}")
                    for kind in (
                        "compound_mentions",
                        "measurements",
                        "assay_descriptions",
                        "optimization_statements",
                        "experimental_names",
                    ):
                        for fact in extraction.get(kind, []):
                            items.append({"kind": kind, "node_ref": node.ref, "fact": fact})
                else:
                    pack.pending_refs.append(node.ref)
        if node.node_type in VISUAL_TYPES:
            report = reports.get(node.ref)
            if (
                report is None
                or report.get("node_ref") != node.ref
                or report.get("document_id") != ir.document_id
                or report.get("mode", "full") != "full"
                or report.get("status") not in {"ok", "remediated", "needs_review"}
            ):
                pack.pending_refs.append(node.ref)
                pack.warnings.append(f"figure pending/failed: {node.ref}")
                continue
            extraction = report.get("extraction", {})
            digest = {
                "kind": "figure",
                "node_ref": node.ref,
                "caption": node.label,
                "image_type": report.get("image_type"),
                "status": report["status"],
                "artifact_ids": report.get("artifact_ids", []),
                "unchecked": report.get("unchecked", []),
                "open_questions": report.get("open_questions", []),
                "molecular_coverage": report.get("molecular_coverage", {}),
                "table_rows": extraction.get("rows", [])[:10],
                "headers": extraction.get("headers", []),
                "molecules": extraction.get("molecules", []),
                "edges": extraction.get("reaction_edges", extraction.get("edges", [])),
            }
            if report["status"] == "needs_review":
                pack.warnings.append(f"figure needs_review: {node.ref}")
            items.append(digest)
    current: list[dict[str, Any]] = []
    for item in items:
        if tokens([item]) > max_tokens:
            # 不截断事实证据或复核状态；调用方可以换更小的摘要或更大的预算。
            raise ValueError(f"page pack item exceeds budget: {item.get('node_ref')}")
        if current and tokens([*current, item]) > max_tokens:
            pack.units.append(ReadingUnit(items=current, estimated_tokens=tokens(current)))
            current = []
        current.append(item)
    if current:
        pack.units.append(ReadingUnit(items=current, estimated_tokens=tokens(current)))
    pack.pending_refs = sorted(set(pack.pending_refs))
    pack.ready = not pack.pending_refs
    pack.estimated_tokens = sum(unit.estimated_tokens for unit in pack.units)
    return pack


async def load_page_pack(
    db: Database, document_id: str, page: int, *, user_id: str = "default", max_tokens: int = 7000
) -> PagePack:
    async with db.session(user_id) as session:
        doc = await session.get(Document, document_id)
        if doc is None or doc.user_id != user_id:
            raise PermissionError("document unavailable")
        ir = await load_ir(session, document_id, doc.active_revision)
        rows = (
            await session.execute(
                select(FigureReportRow)
                .where(
                    FigureReportRow.document_id == document_id,
                    FigureReportRow.user_id == user_id,
                    FigureReportRow.mode == "full",
                )
                .order_by(FigureReportRow.created_at, FigureReportRow.id)
            )
        ).scalars()
        reports = {r.node_ref: r.payload for r in rows}
        artifacts = (
            await session.execute(
                select(ArtifactRow).where(
                    ArtifactRow.document_id == document_id,
                    ArtifactRow.user_id == user_id,
                    ArtifactRow.is_active.is_(True),
                    ArtifactRow.artifact_type == "text_extraction",
                    ArtifactRow.status.in_(["completed", "needs_review"]),
                )
            )
        ).scalars()
        # 正文纠错后，旧产物即使仍为active也不能混入新页包。
        extractions = {
            a.target_ref: a.payload or {} for a in artifacts if (a.payload or {}).get("source_text_sha256")
        }
    return build_page_pack(ir, page, reports=reports, extractions=extractions, max_tokens=max_tokens)
