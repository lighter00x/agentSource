"""节点接口：详情与裁剪图。节点引用是全局引用 `{document_id}/r{rev}/{local_id}`（含 `/`，作为 path 参数）。"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Query
from fastapi.responses import Response

from drugpaper.api.deps import ServicesDep, SessionDep, pdf_ref_of, require_document, split_ref
from drugpaper.api.documents import node_brief
from drugpaper.api.errors import ApiError, ErrorResponse, not_found
from drugpaper.api.schemas import NodeDetail
from drugpaper.domain.nodes import DocumentNode
from drugpaper.parsers.mineru_legacy.line_enricher import aligned_block
from drugpaper.parsers.mineru_legacy.schemas import MiddleDoc, load_middle
from drugpaper.persistence.repos.documents import get_revision
from drugpaper.persistence.repos.nodes import children, get_node

router = APIRouter(tags=["nodes"], responses={400: {"model": ErrorResponse}, 404: {"model": ErrorResponse}})


async def _node(s: SessionDep, node_ref: str) -> DocumentNode:
    doc_id, rev, local = split_ref(node_ref)
    await require_document(s, doc_id)
    n = await get_node(s, doc_id, local, rev)
    if n is None:
        raise not_found("节点", node_ref)
    return n


# 注意：crop 路由必须先于通配的 /nodes/{node_ref:path} 注册
@router.get(
    "/nodes/{node_ref:path}/crop", response_class=Response, responses={200: {"content": {"image/png": {}}}}
)
async def get_node_crop(
    sv: ServicesDep,
    s: SessionDep,
    node_ref: str,
    dpi: Annotated[int, Query(ge=36, le=600)] = 300,
    region: Annotated[int, Query(ge=0, description="第几个 region（续段、跨页 segment）")] = 0,
    pad: Annotated[float, Query(ge=0, le=72, description="四周留白（PDF 点）")] = 0,
) -> Response:
    n = await _node(s, node_ref)
    if region >= len(n.geometry.regions):
        raise ApiError(404, "NOT_FOUND", f"节点只有 {len(n.geometry.regions)} 个 region", region=region)
    pdf = await pdf_ref_of(s, n.document_id)
    ref = await sv.renderer.crop(pdf, n.geometry.regions[region], dpi=dpi, pad=pad)
    return Response(await sv.store.get(ref), media_type="image/png", headers={"X-Blob-Ref": ref})


@router.get("/nodes/{node_ref:path}", response_model=NodeDetail)
async def get_node_detail(sv: ServicesDep, s: SessionDep, node_ref: str) -> NodeDetail:
    """节点详情：全文、regions、子节点摘要（Artifact 摘要在 P3 加入）。"""
    n = await _node(s, node_ref)
    kids = await children(s, n.document_id, n.local_id, n.revision)
    attributes = {k: v for k, v in n.attributes.items() if k != "lines"}
    # Read the original revision, including existing imports, without rewriting OCR or offsets.
    source = n.source_ref
    if n.node_type not in {"figure", "table", "molecule"} and all(
        key in source for key in ("page_idx", "detailed_index", "bbox_pt")
    ):
        revision = await get_revision(s, n.document_id, n.revision)
        ref = revision.raw_blob_refs.get("raw_middle_json") if revision else None
        if ref:
            try:
                middle = load_middle(await sv.store.get(ref))
                attributes.update(ocr_attributes(middle, source))
            except (ValueError, KeyError, OSError):
                attributes["ocr_render_warning"] = "无法读取原始公式片段，当前使用 OCR 原文预览。"

    return NodeDetail(
        node_id=n.ref,
        local_id=n.local_id,
        document_id=n.document_id,
        revision=n.revision,
        type=n.node_type,
        subtype=n.subtype,
        label=n.label,
        page=n.page,
        section=n.section_role,
        reading_order=n.reading_order,
        regions=n.geometry.regions,
        text_raw=n.text_raw,
        text_norm=n.text_norm,
        parent=f"{n.document_id}/r{n.revision}/{n.parent_id}" if n.parent_id else None,
        children=[node_brief(k) for k in kids],
        quality_flags=n.quality_flags,
        attributes=attributes,
        source_ref=n.source_ref,
    )


def ocr_attributes(middle: MiddleDoc, source: dict[str, Any]) -> dict[str, Any]:
    """Only expose typed spans when the original block and layout geometry agree."""
    block, aligned = aligned_block(middle, source["page_idx"], source["detailed_index"], source["bbox_pt"])
    if block is None or not aligned:
        return {"ocr_render_warning": "MinerU 公式片段与布局未对齐，当前使用 OCR 原文预览。"}
    lines = [
        {"spans": [{"type": span.type, "content": span.content or ""} for span in line.spans]}
        for line in block.all_lines()
        if line.spans
    ]
    return {"ocr_lines": lines} if lines else {}
