"""S2.4a/S2.4b：由单元生成节点——原始块、视觉节点及其成员（图注、脚注、图内部件、被吞的正文）。

成员处理（执行方案 S2.4b）：

| 条件 | 处理 |
|---|---|
| 文本匹配标签正则（或 image/table_caption 子块匹配） | `caption` 子节点；组的 label 取自此处 |
| 以脚注标记开头 / table_footnote 子块 | `footnote` 子节点；标记字典存入组的 attributes |
| 非图注文本，> 200 字符或以 ABSTRACT 开头 | 独立 `paragraph`（不挂在组下），`absorbed_by_merge`（F4） |
| image_body / table_body 成员 | `figure_part` 子节点，保留几何 |
| 其余短文本 | `figure_part`，subtype=text |
| 空文本成员 | 不生成节点，留给续段处理（F5） |

组的 `caption_text` 与选出的图注不一致时（F3，如 Table 1 的 `"1-10"`）加 `caption_mismatch`。
"""

from __future__ import annotations

from dataclasses import dataclass

from drugpaper.domain import ids
from drugpaper.domain.geometry import Region
from drugpaper.domain.nodes import DocumentNode
from drugpaper.parsers.mineru_legacy.adapter.context import BuildContext, EmptyBlock, TextSource
from drugpaper.parsers.mineru_legacy.adapter.node_types import (
    RAW_TYPE_MAP,
    TEXT_RAW_TYPES,
    VISUAL_RAW_TYPES,
    Label,
    classify_visual,
    find_label,
    is_footnote_text,
    parse_footnote_marks,
)
from drugpaper.parsers.mineru_legacy.adapter.units import Unit
from drugpaper.parsers.mineru_legacy.schemas import LayoutBlock
from drugpaper.text.normalize import compact_key

ABSORB_MIN_CHARS = 200
ABSTRACT_AREA_MAX_Y = 0.75  # 首页上 3/4 视为摘要区域


@dataclass
class _Child:
    kind: str  # caption / footnote / part
    page: int
    bbox_pt: tuple[float, float, float, float] | None
    text: str | None
    subtype: str | None = None
    source: dict[str, object] | None = None


def build_units(ctx: BuildContext, units: list[Unit]) -> None:
    for u in units:
        if u.kind == "block":
            _build_raw_block(ctx, u)
        else:
            _build_visual(ctx, u)


# ---------------------------------------------------------------- 原始块


def _register_empty(
    ctx: BuildContext, page: int, b: LayoutBlock, order: float, member_of: str | None
) -> None:
    if b.detailed_index is None:
        return
    ctx.empties[(page, b.detailed_index)] = EmptyBlock(
        page=page,
        detailed_index=b.detailed_index,
        bbox_pt=b.bbox,
        block_type=b.type,
        member_of=member_of,
        order=order,
    )


def _add_text_node(
    ctx: BuildContext, page: int, b: LayoutBlock, order: float, *, extra_ref: dict[str, object] | None = None
) -> DocumentNode:
    assert b.detailed_index is not None
    local_id = ids.block_id(page, b.detailed_index)
    node_type = RAW_TYPE_MAP.get(b.type, "paragraph")
    attrs: dict[str, object] = {}
    if b.type == "title":
        attrs["title_level"] = b.title_level
    node = ctx.add(
        local_id,
        node_type,
        order=order,
        regions=[ctx.region(page, b.bbox)],
        text_raw=b.text_str,
        source_ref=ctx.source_ref(page, b, **(extra_ref or {})),
        attributes=attrs,
    )
    ctx.text_sources[local_id] = TextSource(local_id, page, b.detailed_index, b.bbox)
    return node


def _build_raw_block(ctx: BuildContext, u: Unit) -> None:
    b, page = u.anchor, u.page
    extra: dict[str, object] | None = (
        {"dissolved_group": u.dissolved_from} if u.dissolved_from is not None else None
    )
    if b.type in VISUAL_RAW_TYPES:
        _build_visual(ctx, u)
        return
    if b.type in TEXT_RAW_TYPES:
        if not b.text_str:
            _register_empty(ctx, page, b, u.order, None)
            return
        _add_text_node(ctx, page, b, u.order, extra_ref=extra)
        return
    if b.type in RAW_TYPE_MAP:  # formula
        assert b.detailed_index is not None
        ctx.add(
            ids.block_id(page, b.detailed_index),
            RAW_TYPE_MAP[b.type],
            order=u.order,
            regions=[ctx.region(page, b.bbox)],
            text_raw=b.text_str or None,
            source_ref=ctx.source_ref(page, b),
        )
        return
    # 未知结构：保留为 unknown 节点，不丢数据
    assert b.detailed_index is not None
    ctx.add(
        ids.block_id(page, b.detailed_index),
        "unknown",
        order=u.order,
        regions=[ctx.region(page, b.bbox)],
        text_raw=b.text_str or None,
        source_ref=ctx.source_ref(page, b),
    )
    ctx.issue(
        "unknown_block_type", node_id=ids.block_id(page, b.detailed_index), page=page, block_type=b.type
    )


# ---------------------------------------------------------------- 视觉单元


def _visual_id(u: Unit) -> str:
    b = u.anchor
    if u.kind == "cross_group":
        return ids.cross_group_id(u.page, b.confirmed_index or 0)
    if u.kind == "group":
        return ids.group_id(u.page, b.confirmed_index or 0)
    assert b.detailed_index is not None
    return ids.block_id(u.page, b.detailed_index)


def _sub_children(page: int, b: LayoutBlock) -> list[_Child]:
    out: list[_Child] = []
    for s in b.sub_blocks:
        text = (s.text or "").strip()
        if not text:
            continue
        stype = s.type or ""
        kind = "footnote" if stype.endswith("footnote") else "caption"
        out.append(
            _Child(kind=kind, page=s.page_idx if s.page_idx is not None else page, bbox_pt=s.bbox, text=text)
        )
    return out


def _in_abstract_area(ctx: BuildContext, u: Unit) -> bool:
    if u.page != 0:
        return False
    bbox = ctx.norm(u.page, u.anchor.bbox)
    has_abstract = any((m.text_str or "").upper().startswith("ABSTRACT") for _, m in u.members)
    return has_abstract or bbox.y1 <= ABSTRACT_AREA_MAX_Y


def _build_visual(ctx: BuildContext, u: Unit) -> None:
    anchor, page = u.anchor, u.page
    node_id = _visual_id(u)
    children: list[_Child] = []
    absorbed: list[tuple[int, LayoutBlock, float]] = []

    # 1. 成员分类
    for k, (mpage, m) in enumerate(u.members):
        text = m.text_str
        if m.type in VISUAL_RAW_TYPES:
            children.append(
                _Child(
                    "part",
                    mpage,
                    m.bbox,
                    None,
                    subtype="table" if m.type.startswith("table") else "image",
                    source={"detailed_index": m.detailed_index, "img_path": m.img_path},
                )
            )
            children.extend(_sub_children(mpage, m))
            continue
        if not text:
            _register_empty(ctx, mpage, m, u.order + (k + 1) / 1000, node_id)
            continue
        if find_label(text):
            children.append(
                _Child("caption", mpage, m.bbox, text, source={"detailed_index": m.detailed_index})
            )
        elif is_footnote_text(text):
            children.append(
                _Child("footnote", mpage, m.bbox, text, source={"detailed_index": m.detailed_index})
            )
        elif len(text) > ABSORB_MIN_CHARS or text.upper().startswith("ABSTRACT"):
            absorbed.append((mpage, m, u.order - 0.5 + k / 1000))
        else:
            children.append(
                _Child(
                    "part", mpage, m.bbox, text, subtype="text", source={"detailed_index": m.detailed_index}
                )
            )

    # 2. 组/块自身的子块与 caption_text / footnote_text
    own = _sub_children(page, anchor)
    if not u.members:
        children.extend(own)
    else:
        seen = {(c.text, c.bbox_pt) for c in children}
        children.extend(c for c in own if (c.text, c.bbox_pt) not in seen)
    known_texts = {compact_key(c.text) for c in children if c.text}
    if anchor.footnote_text and compact_key(anchor.footnote_text) not in known_texts:
        children.append(_Child("footnote", page, None, anchor.footnote_text.strip()))

    # 3. 标签：第一个匹配标签正则的图注
    label: Label | None = None
    caption_texts: list[str] = []
    for c in children:
        if c.kind != "caption" or not c.text:
            continue
        found = find_label(c.text)
        if found and label is None:
            label = found
        if found:
            caption_texts.append(c.text)
    # 不匹配标签的"图注"：是脚注就改为脚注，否则按原样保留为（无标签）图注或丢弃（F3）
    for c in children:
        if c.kind == "caption" and c.text and not find_label(c.text):
            if is_footnote_text(c.text):
                c.kind = "footnote"
            elif label is not None:
                c.kind = "dropped"
    if label is None and anchor.caption_text and not any(c.kind == "caption" for c in children):
        cap = anchor.caption_text.strip()
        children.append(_Child("footnote" if is_footnote_text(cap) else "caption", page, None, cap))

    node_type, subtype = classify_visual(
        label,
        anchor.type,
        parent_type=anchor.parent_type,
        in_abstract_area=label is None and _in_abstract_area(ctx, u),
    )

    # 4. 视觉节点本身
    if u.kind == "cross_group":
        regions: list[Region] = [ctx.region(page, anchor.bbox, "segment")]
        for mpage, m in u.members:
            if mpage != page:
                regions.append(ctx.region(mpage, m.bbox, "segment"))
    else:
        regions = [ctx.region(page, anchor.bbox)]
    ci = anchor.confirmed_index if anchor.confirmed_index is not None else anchor.detailed_index
    legacy = {"table": "tab"}.get(node_type, "fig")
    image_path = anchor.merged_img_path or anchor.img_path
    attrs: dict[str, object] = {}
    if image_path:
        attrs["image_path"] = image_path
    if anchor.cross_page_body_image_paths:
        attrs["segment_image_paths"] = list(anchor.cross_page_body_image_paths)
    visual = ctx.add(
        node_id,
        node_type,
        order=u.order,
        regions=regions,
        label=label.text if label else None,
        subtype=subtype,
        source_ref=ctx.source_ref(
            page,
            anchor,
            merged_from=anchor.merged_from,
            legacy_id=f"{legacy}_p{page}_{ci}",
            caption_text=anchor.caption_text or None,
        ),
        attributes=attrs,
    )

    # F3：caption_text 字段与选出的图注不一致
    if label is not None and anchor.caption_text:
        cap_key = compact_key(anchor.caption_text)

        def same_prefix(t: str) -> bool:
            k = compact_key(t)
            n = min(40, len(k), len(cap_key))
            return n > 0 and k[:n] == cap_key[:n]

        if not any(same_prefix(t) for t in caption_texts):
            visual.flag("caption_mismatch")
            ctx.issue(
                "caption_mismatch",
                node_id=node_id,
                page=page,
                message=f"caption_text {anchor.caption_text[:40]!r} 与图注 {label.text!r} 不一致",
                caption_text=anchor.caption_text,
            )

    # 5. 子节点
    counters = {"caption": 0, "footnote": 0, "part": 0}
    marks: dict[str, str] = {}
    for i, c in enumerate(children):
        if c.kind == "dropped":
            continue
        k = counters[c.kind]
        counters[c.kind] += 1
        make_id = {"caption": ids.caption_id, "footnote": ids.footnote_id, "part": ids.part_id}[c.kind]
        cid = make_id(node_id, k)
        if c.bbox_pt is not None:
            cregions = [ctx.region(c.page, c.bbox_pt)]
        else:
            cregions = [r.model_copy(update={"derived": True}) for r in regions[:1]]
        ctype = {"caption": "caption", "footnote": "footnote", "part": "figure_part"}[c.kind]
        child = ctx.add(
            cid,
            ctype,  # type: ignore[arg-type]
            order=u.order + (i + 1) / 10_000,
            regions=cregions,
            parent_id=node_id,
            text_raw=c.text,
            subtype=c.subtype,
            label=label.text if (c.kind == "caption" and label and c.text and find_label(c.text)) else None,
            source_ref={
                "page_idx": c.page,
                **({k2: v for k2, v in (c.source or {}).items() if v is not None}),
            },
        )
        # 来自文本成员的图注/脚注也参与行级增强：MinerU 有时把正文续段并进了图注成员
        di = (c.source or {}).get("detailed_index")
        if c.text and isinstance(di, int) and c.bbox_pt is not None and c.kind in ("caption", "footnote"):
            ctx.text_sources[cid] = TextSource(cid, c.page, di, c.bbox_pt)
        if c.kind == "caption":
            ctx.relate(cid, node_id, "caption_of")
        elif c.kind == "footnote":
            ctx.relate(cid, node_id, "footnote_of")
            if c.text:
                for mk, body in parse_footnote_marks(c.text).items():
                    marks.setdefault(mk, body)
        else:
            ctx.relate(node_id, cid, "contains")
        _ = child
    if marks:
        visual.attributes["footnote_marks"] = marks

    # 6. 被吞的正文（F4）：独立段落，不挂在组下
    for mpage, m, order in absorbed:
        para = _add_text_node(ctx, mpage, m, order, extra_ref={"absorbed_by": node_id})
        para.flag("absorbed_by_merge")
        ctx.relate(para.local_id, node_id, "absorbed_from")
        ctx.issue(
            "absorbed_by_merge",
            node_id=para.local_id,
            page=mpage,
            message=f"合并组 {node_id} 吞掉了正文段落，已恢复为独立段落",
            group=node_id,
        )
