"""合成 layout / middle 数据的构造函数（覆盖样例论文中没有出现的结构）。"""

from __future__ import annotations

from typing import Any

PAGE = [612.0, 792.0]


def blk(
    type_: str, bbox: list[float], di: int | None, ci: int | None, text: str = "", **kw: Any
) -> dict[str, Any]:
    return {"type": type_, "bbox": bbox, "detailed_index": di, "confirmed_index": ci, "text": text, **kw}


def page(idx: int, *blocks: dict[str, Any]) -> dict[str, Any]:
    return {"page_idx": idx, "page_size": PAGE, "para_blocks": list(blocks)}


LONG = "Lorem ipsum dolor sit amet, consectetur adipiscing elit. " * 5


def mline(bbox: list[float], text: str, cross_page: bool = False) -> dict[str, Any]:
    span: dict[str, Any] = {"bbox": bbox, "content": text, "type": "text"}
    if cross_page:
        span["cross_page"] = True
    return {"bbox": bbox, "spans": [span]}


def mblock(
    bbox: list[float], lines: list[dict[str, Any]] | None = None, deleted: bool = False
) -> dict[str, Any]:
    b: dict[str, Any] = {"type": "text", "bbox": bbox, "lines": lines or []}
    if deleted:
        b["lines_deleted"] = True
    return b


def middle(
    *pages: list[dict[str, Any]], discarded: dict[int, list[dict[str, Any]]] | None = None
) -> dict[str, Any]:
    return {
        "pdf_info": [
            {
                "page_idx": i,
                "page_size": PAGE,
                "para_blocks": blocks,
                "discarded_blocks": (discarded or {}).get(i, []),
            }
            for i, blocks in enumerate(pages)
        ]
    }
