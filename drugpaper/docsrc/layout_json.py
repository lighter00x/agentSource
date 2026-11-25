"""直接读取 MinerU `*_layout.json` 的最简 DocumentSource（P1）。

规则（执行方案 S1.5）：
- 原始块：`detailed_index` 不为 None，ID 为 `p{page:03d}.b{detailed_index:03d}`；
- 页内合并组主块：`detailed_index` 为 None，ID 为 `p{page:03d}.g{confirmed_index:03d}`；
- 文本取 `text`，图表块与合并组取 `caption_text`（附 `footnote_text`）；空文本块跳过；
- bbox 按 `page_size` 归一化。
跨页合并、图注纠错（F3）、续段（F5）等在 P2 的 IR Builder 中处理。
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import orjson

from drugpaper.docsrc.base import BBox, BlockView, Hit
from drugpaper.docsrc.textnorm import strip_latex

FIGURE_TYPES = {"image", "image_body", "table", "table_body", "chart", "figure"}
SNIPPET_RADIUS = 60


def _as_str(v: Any) -> str:
    return v.strip() if isinstance(v, str) else ""


def _lines_text(b: dict[str, Any]) -> str:
    """兜底：从 lines → spans → content 拼接文本。"""
    out: list[str] = []
    for line in b.get("lines") or []:
        for span in line.get("spans") or []:
            c = span.get("content")
            if isinstance(c, str):
                out.append(c)
    return " ".join(out).strip()


def _block_text(b: dict[str, Any], is_group: bool) -> str:
    btype = str(b.get("type", ""))
    if is_group or btype in FIGURE_TYPES:
        parts = [_as_str(b.get("caption_text")), _as_str(b.get("footnote_text"))]
        text = "\n".join(p for p in parts if p)
        return text or _as_str(b.get("text"))
    return _as_str(b.get("text")) or _lines_text(b)


def _norm_bbox(raw: Any, page_size: Sequence[float] | None) -> BBox | None:
    if not (isinstance(raw, list | tuple) and len(raw) == 4 and page_size and len(page_size) >= 2):
        return None
    w, h = float(page_size[0]), float(page_size[1])
    if w <= 0 or h <= 0:
        return None
    x0, y0, x1, y1 = (float(v) for v in raw)

    def clamp(v: float) -> float:
        return round(min(max(v, 0.0), 1.0), 4)

    return (clamp(x0 / w), clamp(y0 / h), clamp(x1 / w), clamp(y1 / h))


def compile_pattern(pattern: str) -> re.Pattern[str]:
    """IGNORECASE 正则；编译失败时按字面量匹配。"""
    try:
        return re.compile(pattern, re.IGNORECASE)
    except re.error:
        return re.compile(re.escape(pattern), re.IGNORECASE)


def make_snippet(text: str, start: int, end: int, radius: int = SNIPPET_RADIUS) -> str:
    a, b = max(0, start - radius), min(len(text), end + radius)
    s = text[a:b].replace("\n", " ")
    return ("…" if a > 0 else "") + s + ("…" if b < len(text) else "")


class LayoutJsonSource:
    def __init__(self, pages: list[dict[str, Any]], *, document_id: str = "doc") -> None:
        self._document_id = document_id
        self._blocks: dict[str, BlockView] = {}
        self._by_page: dict[int, list[BlockView]] = {}
        self._norm: dict[str, str] = {}  # 块 ID → 去 LaTeX 文本（懒计算，仅用于匹配）
        self._build(pages)

    @classmethod
    def from_file(cls, path: Path | str) -> LayoutJsonSource:
        p = Path(path)
        data = orjson.loads(p.read_bytes())
        if not isinstance(data, list):
            raise ValueError(f"{p} 不是 layout.json：顶层应为页面数组")
        stem = p.stem.removesuffix("_layout")
        return cls(data, document_id=stem)

    # ------------------------------------------------------------ 构建

    def _build(self, pages: list[dict[str, Any]]) -> None:
        for page_obj in pages:
            page = int(page_obj.get("page_idx", 0))
            page_size = page_obj.get("page_size")
            views: list[BlockView] = []
            for b in page_obj.get("para_blocks") or []:
                di, ci = b.get("detailed_index"), b.get("confirmed_index")
                if di is not None:
                    block_id, is_group = f"p{page:03d}.b{int(di):03d}", False
                elif ci is not None:
                    block_id, is_group = f"p{page:03d}.g{int(ci):03d}", True
                else:
                    continue
                text = _block_text(b, is_group)
                if not text:  # 空文本块跳过（P2 作为续段 region 处理）
                    continue
                members = (
                    [f"p{page:03d}.b{int(m):03d}" for m in b.get("merged_from") or []] if is_group else []
                )
                view = BlockView(
                    id=block_id,
                    page=page,
                    type=str(b.get("type", "unknown")),
                    kind="group" if is_group else "block",
                    text=text,
                    bbox=_norm_bbox(b.get("bbox"), page_size),
                    members=members,
                )
                if block_id in self._blocks:  # 源数据异常：同页重复序号，保留第一个
                    continue
                self._blocks[block_id] = view
                views.append(view)
            self._by_page.setdefault(page, []).extend(views)

    # ------------------------------------------------------------ DocumentSource

    @property
    def document_id(self) -> str:
        return self._document_id

    def pages(self) -> list[int]:
        return sorted(self._by_page)

    def blocks(self, page: int | None = None) -> list[BlockView]:
        if page is None:
            return list(self._blocks.values())
        return list(self._by_page.get(page, []))

    def block(self, block_id: str) -> BlockView:
        try:
            return self._blocks[block_id]
        except KeyError:
            raise KeyError(f"块不存在：{block_id}") from None

    def search_text(self, pattern: str, max_results: int = 10) -> list[Hit]:
        """先在原文上匹配；不命中时在去 LaTeX 的文本上再匹配（`D1` 能命中 `\\mathrm { D } _ { 1 }`）。"""
        rx = compile_pattern(pattern)
        hits: list[Hit] = []
        for v in self._blocks.values():
            text = v.text
            m = rx.search(text)
            if m is None:
                text = self._norm.setdefault(v.id, strip_latex(v.text))
                m = rx.search(text)
            if m is None:
                continue
            hits.append(Hit(block_id=v.id, page=v.page, type=v.type, snippet=make_snippet(text, *m.span())))
            if len(hits) >= max_results:
                break
        return hits
