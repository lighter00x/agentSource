"""IR Builder 入口：`build_ir(layout, middle, ...) -> IRDocument`（串起 S2.4a～S2.4d）。"""

from __future__ import annotations

from collections.abc import Iterable

from drugpaper.domain import ids
from drugpaper.domain.nodes import IRDocument, PageInfo, QualityReport
from drugpaper.parsers.mineru_legacy.adapter.context import BuildContext
from drugpaper.parsers.mineru_legacy.adapter.continuation import resolve_continuations
from drugpaper.parsers.mineru_legacy.adapter.front_matter import extract_front_matter
from drugpaper.parsers.mineru_legacy.adapter.members import build_units
from drugpaper.parsers.mineru_legacy.adapter.order import assign_reading_order
from drugpaper.parsers.mineru_legacy.adapter.sections import build_sections
from drugpaper.parsers.mineru_legacy.adapter.units import identify_units
from drugpaper.parsers.mineru_legacy.line_enricher import discarded_text
from drugpaper.parsers.mineru_legacy.quality import filter_contained_visuals, run_checks
from drugpaper.parsers.mineru_legacy.schemas import LayoutPage, MiddleDoc, load_layout, load_middle
from drugpaper.text.normalize import NORM_VERSION, normalize_text

PARSER_NAME = "mineru_legacy"
PARSER_VERSION = "1"


def _discarded(ctx: BuildContext) -> None:
    if ctx.middle is None:
        return
    for mp in ctx.middle.pdf_info:
        if mp.page_idx not in ctx.page_sizes:
            continue
        for k, b in enumerate(mp.discarded_blocks):
            ctx.add(
                ids.discarded_id(mp.page_idx, k),
                "discarded",
                order=1e9 + mp.page_idx * 1000 + k,
                regions=[ctx.region(mp.page_idx, b.bbox)],
                text_raw=discarded_text(b) or None,
                source_ref={"page_idx": mp.page_idx, "discarded_index": k},
            )


def build_ir(
    layout: list[LayoutPage] | bytes | str,
    middle: MiddleDoc | bytes | str | None = None,
    *,
    document_id: str,
    revision: int = 1,
    source_name: str | None = None,
    pdf_page_count: int | None = None,
    scanned_pages: Iterable[int] | None = None,
    available_assets: set[str] | None = None,
    pdf_page_sizes: list[tuple[float, float]] | None = None,
) -> IRDocument:
    if isinstance(layout, list) and all(isinstance(p, LayoutPage) for p in layout):
        pages = layout
    else:
        pages = load_layout(layout)
    mid = middle if isinstance(middle, MiddleDoc) or middle is None else load_middle(middle)
    ctx = BuildContext(
        document_id=document_id, revision=revision, pages=pages, middle=mid, pdf_page_sizes=pdf_page_sizes
    )
    repaired = (mid.model_extra or {}).get("repaired_geometry_paths", []) if mid else []
    if repaired:
        ctx.issue(
            "missing_middle_geometry",
            severity="warning",
            message="Invalid empty middle bboxes were derived from child boxes or excluded; original parse retained",
            example={"paths": repaired[:8]},
        )
    if pdf_page_sizes:
        diffs = [
            (p.page_idx, p.page_size, pdf_page_sizes[p.page_idx])
            for p in pages
            if p.page_idx < len(pdf_page_sizes)
            and max(abs(a - b) for a, b in zip(p.page_size, pdf_page_sizes[p.page_idx], strict=True)) > 1.0
        ]
        if diffs:
            ctx.issue(
                "page_size_mismatch",
                severity="info",
                message=f"{len(diffs)} 页的 layout page_size 与 PDF 页面尺寸不一致，已按 PDF 尺寸归一化",
                example={"page": diffs[0][0], "layout": list(diffs[0][1]), "pdf": list(diffs[0][2])},
            )

    build_units(ctx, identify_units(pages))  # S2.4a / S2.4b
    resolve_continuations(ctx)  # S2.4c
    filter_contained_visuals(ctx)  # 兼容启发式
    _discarded(ctx)  # F9
    build_sections(ctx)  # S2.4d 章节
    meta = extract_front_matter(ctx, source_name)
    assign_reading_order(ctx)
    scanned = sorted(set(scanned_pages or []))
    run_checks(ctx, pdf_page_count=pdf_page_count, scanned_pages=scanned, available_assets=available_assets)

    for n in ctx.nodes.values():
        if n.text_raw:
            n.text_norm = normalize_text(n.text_raw)
            n.attributes["norm_version"] = NORM_VERSION

    nodes = sorted(ctx.nodes.values(), key=lambda n: n.reading_order)
    return IRDocument(
        document_id=document_id,
        revision=revision,
        parser=PARSER_NAME,
        parser_version=PARSER_VERSION,
        pages=[
            PageInfo(
                page=p.page_idx,
                width_pt=ctx.page_sizes[p.page_idx][0],
                height_pt=ctx.page_sizes[p.page_idx][1],
            )
            for p in pages
        ],
        nodes=nodes,
        relations=ctx.relations,
        meta=meta,
        quality=QualityReport(
            issues=ctx.issues,
            page_count_layout=len(pages),
            page_count_pdf=pdf_page_count,
            scanned_pages=scanned,
            middle_json_used=mid is not None,
        ),
    )
