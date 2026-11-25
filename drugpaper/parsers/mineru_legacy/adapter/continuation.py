"""S2.4c：续段与行级增强（F5 空文本块、F6 段落跨栏/跨页）。

有 middle.json 时：
1. 取段落对应 middle 块的 `lines`；带 `cross_page` 标记的行属于后续页；
2. 行中心落在本段 bbox 内 → primary；落在某个空文本块内 → 该空块的 bbox 成为本段的 `continuation` region；
3. 既不在本段也不在任何空块内的行，按页和栏聚成 region（由行级证据得到，不标 derived）；
4. 所有行的归一化 bbox 保存在 `attributes.lines`（供证据高亮到行）。

没有 middle.json，或空块没有被任何段落的行认领时：挂到阅读顺序上最近的前一个非空段落/列表，region 标记 `derived=true`。
空块本身不生成节点；合并组内未被认领的空成员只记录告警。
"""

from __future__ import annotations

from collections import defaultdict

from drugpaper.domain.geometry import BBox, union_bbox
from drugpaper.parsers.mineru_legacy.adapter.context import BuildContext, EmptyBlock
from drugpaper.parsers.mineru_legacy.line_enricher import aligned_block, inside, line_center

CONTINUATION_OWNER_TYPES = frozenset({"paragraph", "list"})


def _find_empty(ctx: BuildContext, page: int, x: float, y: float, owner: str) -> EmptyBlock | None:
    for eb in ctx.empties.values():
        if eb.page != page or (eb.owner not in (None, owner)):
            continue
        if inside(eb.bbox_pt, x, y):
            return eb
    return None


def _add_continuation(ctx: BuildContext, owner: str, eb: EmptyBlock, *, derived: bool) -> None:
    node = ctx.nodes[owner]
    eb.owner = owner
    node.geometry.regions.append(ctx.region(eb.page, eb.bbox_pt, "continuation", derived=derived))
    blocks = node.attributes.setdefault("continuation_blocks", [])
    blocks.append(f"p{eb.page:03d}.b{eb.detailed_index:03d}")
    if derived:
        node.flag("derived_continuation")


def _enrich_with_middle(ctx: BuildContext) -> None:
    assert ctx.middle is not None
    for ts in ctx.text_sources.values():
        node = ctx.nodes.get(ts.local_id)
        if node is None:
            continue
        mb, ok = aligned_block(ctx.middle, ts.page, ts.detailed_index, ts.bbox_pt)
        if mb is None or not ok:
            ctx.issue(
                "middle_misaligned",
                node_id=ts.local_id,
                page=ts.page,
                severity="info",
                message="middle.json 中找不到 bbox 一致的块，跳过行级增强",
            )
            continue
        records: list[dict[str, object]] = []
        hits: dict[tuple[int, int], EmptyBlock] = {}
        stray: dict[tuple[int, str], list[tuple[float, float, float, float]]] = defaultdict(list)
        for line in mb.lines:
            cx, cy = line_center(line)
            candidates = [ts.page + 1, ts.page + 2] if line.cross_page else [ts.page]
            placed: int | None = None
            for cp in candidates:
                if cp not in ctx.page_sizes:
                    continue
                if cp == ts.page and inside(ts.bbox_pt, cx, cy):
                    placed = cp
                    break
                eb = _find_empty(ctx, cp, cx, cy, ts.local_id)
                if eb is not None:
                    hits[(eb.page, eb.detailed_index)] = eb
                    placed = cp
                    break
            if placed is None:
                placed = next((p for p in candidates if p in ctx.page_sizes), ts.page)
                col = "L" if cx < ctx.page_sizes[placed][0] / 2 else "R"
                stray[(placed, col)].append(line.bbox)
            records.append({"page": placed, "bbox": list(ctx.norm(placed, line.bbox).as_tuple())})
        for eb in sorted(hits.values(), key=lambda e: (e.page, e.order)):
            _add_continuation(ctx, ts.local_id, eb, derived=False)
        if hits and node.node_type in ("caption", "footnote"):
            node.flag("caption_absorbed_text")
            ctx.issue(
                "caption_absorbed_text",
                node_id=ts.local_id,
                page=ts.page,
                message="图注/脚注成员中混入了正文续段（行级证据落在空文本块内）",
            )
        for (page, _col), boxes in sorted(stray.items()):
            union = union_bbox([BBox(x0=b[0], y0=b[1], x1=b[2], y1=b[3]) for b in boxes])
            node.geometry.regions.append(ctx.region(page, union.as_tuple(), "continuation"))
        if records:
            node.attributes["lines"] = records


def _fallback(ctx: BuildContext) -> None:
    owners = sorted(
        (nid for nid, n in ctx.nodes.items() if n.node_type in CONTINUATION_OWNER_TYPES and n.text_raw),
        key=lambda nid: ctx.order[nid],
    )
    for eb in sorted(ctx.empties.values(), key=lambda e: e.order):
        if eb.owner is not None:
            continue
        if eb.member_of is not None:
            ctx.issue(
                "empty_member_unclaimed",
                node_id=eb.member_of,
                page=eb.page,
                severity="info",
                message=f"合并组成员 p{eb.page:03d}.b{eb.detailed_index:03d} 为空且未被任何段落认领",
            )
            continue
        prev = [nid for nid in owners if ctx.order[nid] < eb.order]
        if not prev:
            ctx.issue(
                "orphan_empty_block", page=eb.page, message=f"空块 b{eb.detailed_index:03d} 之前没有段落"
            )
            continue
        _add_continuation(ctx, prev[-1], eb, derived=True)
        ctx.issue(
            "derived_continuation",
            node_id=prev[-1],
            page=eb.page,
            severity="info",
            message=f"空块 p{eb.page:03d}.b{eb.detailed_index:03d} 按阅读顺序推导为续段",
        )


def resolve_continuations(ctx: BuildContext) -> None:
    if ctx.middle is not None:
        _enrich_with_middle(ctx)
    _fallback(ctx)
