"""S2.4d：前置信息——标题、作者、`Cite This` 行、DOI（设计文档 7.3 第 7 步）。"""

from __future__ import annotations

import re

from drugpaper.domain import ids
from drugpaper.domain.nodes import DocumentMeta
from drugpaper.parsers.mineru_legacy.adapter.context import BuildContext
from drugpaper.text.normalize import normalize_text

DOI_RE = re.compile(r"\b(10\.\d{4,9}/[^\s\"<>,;]+[^\s\"<>,;.)])")
CITE_RE = re.compile(
    r"Cite This:\s*(?P<journal>.+?)\s+(?P<year>(?:19|20)\d{2})\s*,\s*(?P<volume>\d+)\s*,\s*(?P<pages>[\d\-]+)"
)
FILENAME_DOI_RE = re.compile(r"^10_(\d{4,9})_(.+)$")


def doi_from_filename(name: str | None) -> str | None:
    """`10_1021_acs_jmedchem_2c00949` → `10.1021/acs.jmedchem.2c00949`（MinerU 输出目录名的惯例）。"""
    if not name:
        return None
    m = FILENAME_DOI_RE.match(name)
    return f"10.{m.group(1)}/{m.group(2).replace('_', '.')}" if m else None


def _authors(text: str) -> list[str]:
    t = re.sub(r"[*†‡§¶#]+", "", text)
    parts = [re.sub(r"^and\s+", "", p.strip()) for p in re.split(r",\s*|\s+and\s+", t)]
    return [p for p in parts if len(p) > 2 and not re.search(r"\d", p)]


def extract_front_matter(ctx: BuildContext, source_name: str | None) -> DocumentMeta:
    meta = DocumentMeta()
    front = ctx.nodes.get(ids.FRONT_ID)
    children = [ctx.nodes[c] for c in (front.children if front else [])]
    children.sort(key=lambda n: ctx.order[n.local_id])

    title_node = next((n for n in children if n.node_type == "heading"), None)
    if title_node is not None:
        title_node.subtype = "title"
        meta.title = normalize_text(title_node.text_raw)
        after = children[children.index(title_node) + 1 :]
        author_node = next(
            (n for n in after[:2] if n.node_type == "paragraph" and (n.text_raw or "").count(",") >= 1), None
        )
        if author_node is not None and not (author_node.text_raw or "").startswith("Cite"):
            author_node.subtype = "authors"
            meta.authors = _authors(normalize_text(author_node.text_raw))
    for n in children:
        norm = normalize_text(n.text_raw)
        m = CITE_RE.search(norm)
        if m:
            n.subtype = "citation"
            meta.journal = m.group("journal").strip()
            meta.year = int(m.group("year"))
            meta.volume = m.group("volume")
            meta.pages = m.group("pages")
            break

    # DOI：前置信息与前两页文本 → 文件名
    candidates = [n for n in ctx.nodes.values() if n.text_raw and (n.page or 0) <= 1]
    candidates.sort(key=lambda n: ctx.order.get(n.local_id, 0))
    for n in candidates:
        m = DOI_RE.search(normalize_text(n.text_raw))
        if m:
            meta.doi, meta.doi_source = m.group(1), "text"
            break
    if meta.doi is None:
        doi = doi_from_filename(source_name)
        if doi:
            meta.doi, meta.doi_source = doi, "filename"
    return meta
