"""IR 持久化与查询（执行方案 S2.5）。"""

from __future__ import annotations

from typing import Any

from sqlalchemy import delete, insert, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from drugpaper.domain import ids
from drugpaper.domain.geometry import Geometry
from drugpaper.domain.nodes import DocumentMeta, DocumentNode, IRDocument, PageInfo, QualityReport, Relation
from drugpaper.persistence.models import Document, NodeRow, RelationRow, utcnow

INSERT_BATCH = 500


def node_to_row(n: DocumentNode, user_id: str = "default") -> dict[str, Any]:
    return {
        "document_id": n.document_id,
        "revision": n.revision,
        "local_id": n.local_id,
        "user_id": user_id,
        "node_type": n.node_type,
        "subtype": n.subtype,
        "parent_local_id": n.parent_id,
        "children": list(n.children),
        "reading_order": n.reading_order,
        "page": n.page,
        "geometry": n.geometry.model_dump(mode="json"),
        "label": n.label,
        "text_raw": n.text_raw,
        "text_norm": n.text_norm,
        "section_role": n.section_role,
        "source": n.source,
        "source_ref": n.source_ref,
        "quality_flags": list(n.quality_flags),
        "status": n.status,
        "attributes": n.attributes,
    }


def row_to_node(r: NodeRow) -> DocumentNode:
    return DocumentNode(
        document_id=r.document_id,
        revision=r.revision,
        local_id=r.local_id,
        node_type=r.node_type,
        subtype=r.subtype,
        parent_id=r.parent_local_id,
        children=list(r.children or []),
        reading_order=r.reading_order,
        geometry=Geometry.model_validate(r.geometry or {}),
        label=r.label,
        text_raw=r.text_raw,
        text_norm=r.text_norm,
        section_role=r.section_role,
        source=r.source,
        source_ref=dict(r.source_ref or {}),
        quality_flags=list(r.quality_flags or []),
        status=r.status,
        attributes=dict(r.attributes or {}),
    )


async def save_ir(s: AsyncSession, ir: IRDocument, *, user_id: str = "default") -> None:
    """单事务写入：先删除同 (document, revision) 的旧节点与关系，再批量插入；更新文档元数据与质量报告。"""
    doc_id, rev = ir.document_id, ir.revision
    await s.execute(delete(NodeRow).where(NodeRow.document_id == doc_id, NodeRow.revision == rev))
    await s.execute(delete(RelationRow).where(RelationRow.document_id == doc_id, RelationRow.revision == rev))
    rows = [node_to_row(n, user_id) for n in ir.nodes]
    for i in range(0, len(rows), INSERT_BATCH):
        await s.execute(insert(NodeRow), rows[i : i + INSERT_BATCH])
    rel_rows = [
        {
            "user_id": user_id,
            "document_id": doc_id,
            "revision": rev,
            "source": r.source,
            "target": r.target,
            "type": r.type,
            "evidence": r.evidence,
            "status": r.status,
        }
        for r in ir.relations
    ]
    if rel_rows:
        await s.execute(insert(RelationRow), rel_rows)
    await s.execute(
        update(Document)
        .where(Document.id == doc_id)
        .values(
            title=ir.meta.title,
            doi=ir.meta.doi,
            active_revision=rev,
            meta={
                **ir.meta.model_dump(mode="json"),
                "page_info": [p.model_dump() for p in ir.pages],
                "parser": ir.parser,
                "parser_version": ir.parser_version,
                "ir_version": ir.ir_version,
            },
            quality_report=ir.quality.model_dump(mode="json"),
            updated_at=utcnow(),
        )
    )
    await s.flush()
    if s.get_bind().dialect.name == "postgresql":
        await update_tsv(s, doc_id, rev)


async def update_tsv(s: AsyncSession, document_id: str, revision: int) -> None:
    await s.execute(
        text(
            "UPDATE nodes SET tsv = to_tsvector('simple', coalesce(label, '') || ' ' || coalesce(text_norm, '')) "
            "WHERE document_id = :d AND revision = :r"
        ),
        {"d": document_id, "r": revision},
    )


async def active_revision(s: AsyncSession, document_id: str) -> int | None:
    doc = await s.get(Document, document_id)
    return doc.active_revision if doc is not None else None


async def _rev(s: AsyncSession, document_id: str, revision: int | None) -> int:
    if revision is not None:
        return revision
    rev = await active_revision(s, document_id)
    if rev is None:
        raise KeyError(f"文档 {document_id} 还没有 IR")
    return rev


async def load_ir(s: AsyncSession, document_id: str, revision: int | None = None) -> IRDocument:
    rev = await _rev(s, document_id, revision)
    doc = await s.get(Document, document_id)
    assert doc is not None
    rows = (
        await s.execute(
            select(NodeRow)
            .where(NodeRow.document_id == document_id, NodeRow.revision == rev)
            .order_by(NodeRow.reading_order)
        )
    ).scalars()
    rels = (
        await s.execute(
            select(RelationRow).where(RelationRow.document_id == document_id, RelationRow.revision == rev)
        )
    ).scalars()
    meta = dict(doc.meta or {})
    pages = [PageInfo.model_validate(p) for p in meta.pop("page_info", [])]
    parser = meta.pop("parser", "mineru_legacy")
    parser_version = meta.pop("parser_version", "1")
    ir_version = meta.pop("ir_version", "p2.1")
    return IRDocument(
        document_id=document_id,
        revision=rev,
        parser=parser,
        parser_version=parser_version,
        ir_version=ir_version,
        pages=pages,
        nodes=[row_to_node(r) for r in rows],
        relations=[
            Relation(
                source=r.source, target=r.target, type=r.type, evidence=r.evidence or {}, status=r.status
            )
            for r in rels
        ],
        meta=DocumentMeta.model_validate({k: v for k, v in meta.items() if k in DocumentMeta.model_fields}),
        quality=QualityReport.model_validate(doc.quality_report or {}),
    )


async def get_node(
    s: AsyncSession, document_id: str, local_id: str, revision: int | None = None
) -> DocumentNode | None:
    rev = await _rev(s, document_id, revision)
    row = await s.get(NodeRow, (document_id, rev, local_id))
    return row_to_node(row) if row is not None else None


async def children(
    s: AsyncSession, document_id: str, local_id: str, revision: int | None = None
) -> list[DocumentNode]:
    rev = await _rev(s, document_id, revision)
    rows = (
        await s.execute(
            select(NodeRow)
            .where(
                NodeRow.document_id == document_id,
                NodeRow.revision == rev,
                NodeRow.parent_local_id == local_id,
            )
            .order_by(NodeRow.reading_order)
        )
    ).scalars()
    return [row_to_node(r) for r in rows]


async def nodes_on_page(
    s: AsyncSession,
    document_id: str,
    page: int,
    revision: int | None = None,
    *,
    include_discarded: bool = False,
) -> list[DocumentNode]:
    """页内节点：第一个 region 在该页，或任一 region（续段、跨页 segment）落在该页。"""
    rev = await _rev(s, document_id, revision)
    rows = (
        await s.execute(
            select(NodeRow)
            .where(NodeRow.document_id == document_id, NodeRow.revision == rev)
            .order_by(NodeRow.reading_order)
        )
    ).scalars()
    out = []
    for r in rows:
        if r.node_type == "discarded" and not include_discarded:
            continue
        regions = (r.geometry or {}).get("regions") or []
        if r.page == page or any(reg.get("page") == page for reg in regions):
            out.append(row_to_node(r))
    return out


async def context_window(
    s: AsyncSession,
    document_id: str,
    local_id: str,
    *,
    before: int = 1,
    after: int = 1,
    revision: int | None = None,
) -> list[DocumentNode]:
    """同一父节点下、按阅读顺序的前后若干个兄弟节点（含自身）。"""
    node = await get_node(s, document_id, local_id, revision)
    if node is None:
        return []
    if node.parent_id is None:
        return [node]
    sibs = await children(s, document_id, node.parent_id, node.revision)
    i = next(k for k, n in enumerate(sibs) if n.local_id == local_id)
    return sibs[max(0, i - before) : i + after + 1]


def tree_from_nodes(nodes: list[DocumentNode]) -> dict[str, Any]:
    """轻量树：只含 ID、类型、子类型、标签、页码、章节、子节点（不含全文）。"""
    by_id = {n.local_id: n for n in nodes}

    def build(nid: str) -> dict[str, Any]:
        n = by_id[nid]
        item: dict[str, Any] = {"id": nid, "type": n.node_type}
        if n.subtype:
            item["subtype"] = n.subtype
        if n.label:
            item["label"] = n.label
        if n.page is not None:
            item["page"] = n.page
        if n.node_type == "section":
            item["role"] = n.section_role
        if n.node_type == "heading" and n.text_norm:
            item["text"] = n.text_norm[:120]
        kids = [build(c) for c in n.children if c in by_id]
        if kids:
            item["children"] = kids
        return item

    if ids.ROOT_ID not in by_id:
        return {"id": ids.ROOT_ID, "type": "document", "children": []}
    return build(ids.ROOT_ID)


async def get_tree(s: AsyncSession, document_id: str, revision: int | None = None) -> dict[str, Any]:
    ir = await load_ir(s, document_id, revision)
    return tree_from_nodes(ir.nodes)
