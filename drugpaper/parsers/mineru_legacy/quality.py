"""S2.4d：质检与兼容启发式（设计文档 7.3 第 9、10 步）。"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable

from drugpaper.domain.geometry import iou, overlap_ratio
from drugpaper.domain.nodes import VISUAL_TYPES
from drugpaper.parsers.mineru_legacy.adapter.context import BuildContext

OVERLAP_IOU = 0.6
CONTAIN_RATIO = 0.8
STRUCTURAL = frozenset({"document", "front_matter", "section", "discarded"})


def filter_contained_visuals(ctx: BuildContext) -> None:
    """同页"大框完全包含小框且重叠比例 > 0.8"时只保留大框作为视觉节点，小框降为 figure_part。"""
    by_page: dict[int, list[str]] = defaultdict(list)
    for nid, n in ctx.nodes.items():
        if n.node_type in VISUAL_TYPES and n.parent_id is None and n.geometry.regions:
            by_page[n.geometry.regions[0].page].append(nid)
    for nids in by_page.values():
        nids.sort(key=lambda i: -ctx.nodes[i].geometry.regions[0].bbox.area)
        for i, small_id in enumerate(nids):
            small = ctx.nodes[small_id]
            if small.parent_id is not None:
                continue
            sb = small.geometry.regions[0].bbox
            for big_id in nids[:i]:
                big = ctx.nodes[big_id]
                if big.parent_id is not None:
                    continue
                if overlap_ratio(sb, big.geometry.regions[0].bbox) > CONTAIN_RATIO:
                    small.source_ref["original_type"] = small.node_type
                    small.node_type = "figure_part"
                    small.subtype = "contained_visual"
                    small.flag("contained_in_larger_box")
                    ctx.reparent(small_id, big_id)
                    ctx.relate(big_id, small_id, "contains")
                    ctx.issue(
                        "contained_visual",
                        node_id=small_id,
                        severity="info",
                        message=f"{small_id} 被 {big_id} 包含，已降为 figure_part",
                    )
                    break


def run_checks(
    ctx: BuildContext,
    *,
    pdf_page_count: int | None,
    scanned_pages: Iterable[int] | None,
    available_assets: set[str] | None,
) -> None:
    # bbox 越界
    for nid, n in ctx.nodes.items():
        for r in n.geometry.regions:
            if r.source_bbox_pt is not None and ctx.out_of_bounds(r.page, r.source_bbox_pt):
                n.flag("bbox_out_of_bounds")
                ctx.issue("bbox_out_of_bounds", node_id=nid, page=r.page, bbox_pt=list(r.source_bbox_pt))
                break

    # 页内大面积重叠（非父子、非结构节点、只看主 region）
    by_page: dict[int, list[str]] = defaultdict(list)
    for nid, n in ctx.nodes.items():
        if n.node_type in STRUCTURAL or not n.geometry.regions or n.geometry.regions[0].derived:
            continue
        if n.parent_id is not None and ctx.nodes[n.parent_id].node_type not in STRUCTURAL:
            continue  # 视觉节点的子节点不参与
        by_page[n.geometry.regions[0].page].append(nid)
    for page, nids in by_page.items():
        for i, a in enumerate(nids):
            for b in nids[i + 1 :]:
                v = iou(ctx.nodes[a].geometry.regions[0].bbox, ctx.nodes[b].geometry.regions[0].bbox)
                if v > OVERLAP_IOU:
                    ctx.issue("overlap", node_id=a, page=page, other=b, iou=round(v, 3))

    # 资产缺失
    if available_assets is not None:
        for nid, n in ctx.nodes.items():
            for key in ("image_path",):
                path = n.attributes.get(key)
                if path and path not in available_assets:
                    n.flag("asset_missing")
                    ctx.issue("asset_missing", node_id=nid, page=n.page, path=path)

    # 页数
    if pdf_page_count is not None and pdf_page_count != len(ctx.pages):
        ctx.issue(
            "page_count_mismatch",
            severity="error",
            message=f"layout.json 有 {len(ctx.pages)} 页，PDF 有 {pdf_page_count} 页",
        )

    # 扫描页
    for p in scanned_pages or []:
        ctx.issue("scanned_page", page=p, message="该页没有文本层，下游文本抽取会更依赖 VLM")
