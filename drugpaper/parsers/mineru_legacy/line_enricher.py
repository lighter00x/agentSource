"""middle.json 行级增强：layout 原始块 `(page_idx, detailed_index)` ↔ middle `pdf_info[page].para_blocks[detailed_index]`。"""

from __future__ import annotations

from collections.abc import Sequence

from drugpaper.parsers.mineru_legacy.schemas import MiddleBlock, MiddleDoc, MiddleLine

ALIGN_TOL_PT = 2.5


def middle_block(middle: MiddleDoc, page: int, detailed_index: int) -> MiddleBlock | None:
    p = middle.page(page)
    if p is None or not (0 <= detailed_index < len(p.para_blocks)):
        return None
    return p.para_blocks[detailed_index]


def bbox_close(a: Sequence[float], b: Sequence[float], tol: float = ALIGN_TOL_PT) -> bool:
    return all(abs(float(x) - float(y)) <= tol for x, y in zip(a, b, strict=True))


def aligned_block(
    middle: MiddleDoc, page: int, detailed_index: int, layout_bbox: Sequence[float]
) -> tuple[MiddleBlock | None, bool]:
    """返回 (middle 块, 是否对齐)。bbox 不一致时不对齐（由调用方记录告警）。"""
    mb = middle_block(middle, page, detailed_index)
    if mb is None:
        return None, False
    return mb, bbox_close(mb.bbox, layout_bbox)


def line_center(line: MiddleLine) -> tuple[float, float]:
    x0, y0, x1, y1 = line.bbox
    return ((x0 + x1) / 2, (y0 + y1) / 2)


def inside(bbox: Sequence[float], x: float, y: float, tol: float = 3.0) -> bool:
    return bbox[0] - tol <= x <= bbox[2] + tol and bbox[1] - tol <= y <= bbox[3] + tol


def discarded_text(block: MiddleBlock) -> str:
    return " ".join(line.text.strip() for line in block.all_lines() if line.text.strip())
