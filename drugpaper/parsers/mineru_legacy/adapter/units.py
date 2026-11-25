"""S2.4a：识别顶层单元（设计文档 7.3 第 2 步）。

- 原始顶层块：`detailed_index` 不为 None，且不是任何合并组的成员；
- 页内合并组：`merged_from` 存在；成员 = `merged_from` 中的块 ∪ `belong_to == confirmed_index` 的块
  （样例论文中很多成员没有 `belong_to`，只能靠 `merged_from` 反查）；
- 跨页合并组：`merged_from_cross` 存在；成员由 `merged_from_cross` 与 `belong_to_cross` 反查；
- 纯文本合并组（组类型为 text/title/list 且成员中没有图注标签）：解散，成员按顶层块处理。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from drugpaper.parsers.mineru_legacy.adapter.node_types import TEXT_RAW_TYPES, find_label
from drugpaper.parsers.mineru_legacy.schemas import LayoutBlock, LayoutPage

UnitKind = Literal["block", "group", "cross_group"]


@dataclass
class Unit:
    kind: UnitKind
    page: int
    anchor: LayoutBlock
    order: float
    members: list[tuple[int, LayoutBlock]] = field(default_factory=list)
    dissolved_from: int | None = None  # 被解散的纯文本合并组的 confirmed_index


def _pairs(value: Any) -> list[tuple[int, int]]:
    """把 merged_from_cross / belong_to_cross 的各种写法统一成 [(page, index)]。"""
    out: list[tuple[int, int]] = []
    if value is None:
        return out
    items = value if isinstance(value, list) and value and not isinstance(value[0], int) else [value]
    for it in items:
        if isinstance(it, dict):
            page = it.get("page_idx", it.get("page"))
            idx = it.get("detailed_index", it.get("confirmed_index", it.get("index")))
            if page is not None and idx is not None:
                out.append((int(page), int(idx)))
        elif isinstance(it, list | tuple) and len(it) >= 2:
            out.append((int(it[0]), int(it[1])))
    return out


def identify_units(pages: list[LayoutPage]) -> list[Unit]:
    by_page: dict[int, LayoutPage] = {p.page_idx: p for p in pages}
    claimed: set[tuple[int, int]] = set()  # (page, detailed_index) 已作为成员
    units: list[Unit] = []

    # 先处理跨页组，登记成员
    cross_anchors: list[tuple[int, int, LayoutBlock]] = []
    for p in pages:
        for pos, b in enumerate(p.para_blocks):
            if b.merged_from_cross:
                cross_anchors.append((p.page_idx, pos, b))
    for page, pos, anchor in cross_anchors:
        members: list[tuple[int, LayoutBlock]] = []
        wanted = set(_pairs(anchor.merged_from_cross))
        for other in pages:
            for b in other.para_blocks:
                if b.detailed_index is None:
                    continue
                key = (other.page_idx, b.detailed_index)
                back = _pairs(b.belong_to_cross)
                if key in wanted or (page, anchor.confirmed_index or -1) in back:
                    members.append((other.page_idx, b))
                    claimed.add(key)
        units.append(
            Unit(kind="cross_group", page=page, anchor=anchor, order=page * 10_000 + pos, members=members)
        )

    for p in pages:
        page = p.page_idx
        blocks = p.para_blocks
        # 页内组成员
        groups: list[tuple[int, LayoutBlock, list[LayoutBlock]]] = []
        for pos, b in enumerate(blocks):
            if b.merged_from_cross or b.detailed_index is not None or b.confirmed_index is None:
                continue
            idxs = set(b.merged_from or [])
            mem = [
                m
                for m in blocks
                if m.detailed_index is not None
                and (
                    m.detailed_index in idxs
                    or (m.belong_to == b.confirmed_index and m.confirmed_index is None)
                )
            ]
            for m in mem:
                claimed.add((page, m.detailed_index))  # type: ignore[arg-type]
            groups.append((pos, b, mem))

        for pos, anchor, mem in groups:
            has_label = any(find_label(m.text) for m in mem) or bool(find_label(anchor.caption_text))
            if anchor.type in TEXT_RAW_TYPES and not has_label:
                # 纯文本合并组：解散
                for k, m in enumerate(mem):
                    units.append(
                        Unit(
                            kind="block",
                            page=page,
                            anchor=m,
                            order=page * 10_000 + pos + (k + 1) / 100,
                            dissolved_from=anchor.confirmed_index,
                        )
                    )
                continue
            units.append(
                Unit(
                    kind="group",
                    page=page,
                    anchor=anchor,
                    order=page * 10_000 + pos,
                    members=[(page, m) for m in mem],
                )
            )

        for pos, b in enumerate(blocks):
            if b.detailed_index is None or b.merged_from_cross:
                continue
            if (page, b.detailed_index) in claimed:
                continue
            units.append(Unit(kind="block", page=page, anchor=b, order=page * 10_000 + pos))
    _ = by_page
    units.sort(key=lambda u: u.order)
    return units
