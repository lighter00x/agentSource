"""S2.4d：章节树与段落内小标题（设计文档 7.3 第 6 步）。

- magic_pdf 1.x 的 `title_level` 常常全是 1：全大写且长度 < 60 的标题 → 一级章节；其余标题 → 章节内二级标题；
- 句首短句小标题（`Identification of Compound 3.`）→ `heading` 节点（subtype=run_in），不开新章节，段落文本不变；
- section_role 按词典判定，无法判定时继承上一章节；References 之后的内容标为 references（保留在树中）。
"""

from __future__ import annotations

import re

from drugpaper.domain import ids
from drugpaper.domain.nodes import DocumentNode
from drugpaper.parsers.mineru_legacy.adapter.context import BuildContext
from drugpaper.text.normalize import normalize_detail, normalize_text

SECTION_ROLES: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"^ABSTRACT\b"), "abstract"),
    (re.compile(r"^(INTRODUCTION|BACKGROUND)\b"), "introduction"),
    (re.compile(r"^RESULTS?\b"), "results"),
    (re.compile(r"^DISCUSSION\b"), "discussion"),
    (re.compile(r"^(CONCLUSIONS?|CONCLUDING)\b"), "conclusion"),
    (re.compile(r"^(EXPERIMENTAL|MATERIALS AND METHODS|METHODS|METHODOLOGY)\b"), "experimental"),
    (re.compile(r"^(REFERENCES?|BIBLIOGRAPHY|LITERATURE CITED)\b"), "references"),
    (re.compile(r"^ACKNOWLEDG"), "acknowledgments"),
    (re.compile(r"^(ASSOCIATED CONTENT|SUPPORTING INFORMATION|SUPPLEMENTARY)"), "supporting_information"),
    (re.compile(r"^ABBREVIATIONS?\b"), "abbreviations"),
    (re.compile(r"^AUTHOR INFORMATION\b"), "author_information"),
]

RUN_IN_RE = re.compile(r"^([A-Z][^.,;:\[\]()]{1,78}?\.)(?=\s*[A-Z(\[0-9])")
RUN_IN_EXCLUDE = re.compile(r"^(Figure|Fig|Table|Scheme|Chart)\b")
SKIP_RUN_IN_ROLES = frozenset({"front_matter", "references", "author_information", "abbreviations"})


def role_of_heading(text: str) -> str | None:
    t = normalize_text(text).upper().lstrip("•*· ")
    for pat, role in SECTION_ROLES:
        if pat.match(t):
            return role
    return None


def is_section_heading(text: str) -> bool:
    t = normalize_text(text).lstrip("•*· ")
    letters = [c for c in t if c.isalpha()]
    return len(letters) >= 3 and len(t) < 60 and all(c.isupper() for c in letters)


def run_in_heading(norm: str) -> str | None:
    m = RUN_IN_RE.match(norm)
    if m is None:
        return None
    heading = m.group(1)
    if RUN_IN_EXCLUDE.match(heading) or len(heading) < 5:
        return None
    words = heading[:-1].split()
    if not words or len(words) > 12:
        return None
    long_words = [w for w in words if len(w) > 3]
    if long_words and sum(w[0].isupper() for w in long_words) / len(long_words) < 0.6:
        return None
    return heading


def _front_end(ctx: BuildContext, top: list[DocumentNode]) -> int:
    """首页上"摘要或第一个已知章节标题"之前的节点都是前置信息。"""
    for i, n in enumerate(top):
        if n.page != 0:
            return i
        text = n.text_raw or ""
        if n.node_type == "paragraph" and normalize_text(text).upper().startswith("ABSTRACT"):
            return i
        if n.node_type == "heading" and is_section_heading(text) and role_of_heading(text) is not None:
            return i
        if n.node_type in ("figure", "scheme", "table") and n.subtype == "toc_graphic":
            return i
    return 0


def build_sections(ctx: BuildContext) -> None:
    root = ctx.add(ids.ROOT_ID, "document", order=-2)
    top = sorted(
        (
            n
            for n in ctx.nodes.values()
            if n.parent_id is None and n.local_id != ids.ROOT_ID and n.node_type != "discarded"
        ),
        key=lambda n: ctx.order[n.local_id],
    )
    end = _front_end(ctx, top)
    if end > 0:
        ctx.add(ids.FRONT_ID, "front_matter", order=-1, parent_id=root.local_id)
        for n in top[:end]:
            ctx.reparent(n.local_id, ids.FRONT_ID)
            n.section_role = "front_matter"
        ctx.nodes[ids.FRONT_ID].section_role = "front_matter"

    section: DocumentNode | None = None
    role = "body"
    k = 0

    def new_section(first: DocumentNode, label: str | None, sec_role: str) -> DocumentNode:
        nonlocal k
        sec = ctx.add(
            ids.section_id(k),
            "section",
            order=ctx.order[first.local_id] - 1e-6,
            parent_id=root.local_id,
            label=label,
            regions=[r.model_copy() for r in first.geometry.regions[:1]],
        )
        sec.section_role = sec_role
        k += 1
        return sec

    for n in top[end:]:
        text = n.text_raw or ""
        if n.node_type == "heading" and is_section_heading(text):
            role = role_of_heading(text) or role
            section = new_section(n, normalize_text(text).lstrip("•*· "), role)
            n.attributes["level"] = 1
        elif (
            section is None
            and n.node_type == "paragraph"
            and normalize_text(text).upper().startswith("ABSTRACT")
        ):
            role = "abstract"
            section = new_section(n, "ABSTRACT", role)
        elif section is None:
            section = new_section(n, None, role)
        elif n.node_type == "heading":
            n.attributes["level"] = 2
        ctx.reparent(n.local_id, section.local_id)

    # section_role 下传到整棵子树
    def assign(node: DocumentNode, r: str | None) -> None:
        node.section_role = r
        for c in list(node.children):
            assign(ctx.nodes[c], r)

    for cid in list(root.children):
        c = ctx.nodes[cid]
        assign(c, c.section_role)

    _run_in_headings(ctx)


def _run_in_headings(ctx: BuildContext) -> None:
    for n in list(ctx.nodes.values()):
        if n.node_type != "paragraph" or not n.text_raw or n.section_role in SKIP_RUN_IN_ROLES:
            continue
        detail = normalize_detail(n.text_raw)
        heading = run_in_heading(detail.norm)
        if heading is None:
            continue
        s, e = detail.map_span(0, len(heading))
        lines = n.attributes.get("lines") or []
        if lines:
            first = lines[0]
            from drugpaper.domain.geometry import BBox, Region

            bb = first["bbox"]
            regions = [
                Region(page=first["page"], bbox=BBox(x0=bb[0], y0=bb[1], x1=bb[2], y1=bb[3]), role="primary")
            ]
        else:
            regions = [r.model_copy(update={"derived": True}) for r in n.geometry.regions[:1]]
        h = ctx.add(
            ids.run_in_heading_id(n.local_id),
            "heading",
            order=ctx.order[n.local_id] - 1e-4,
            parent_id=n.parent_id,
            text_raw=n.text_raw[s:e],
            subtype="run_in",
            regions=regions,
            source_ref={"run_in_of": n.local_id, "char_span": [s, e]},
            attributes={"level": 3},
        )
        h.section_role = n.section_role
        n.attributes["run_in_heading"] = h.local_id
