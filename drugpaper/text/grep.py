"""全文 grep（设计文档 7.9.3 节，执行方案 S2.9）。

在 `text_norm` 上用 `regex` 库匹配（带超时，防灾难性回溯），命中位置通过偏移映射回 `text_raw`，
作为证据的 `char_span`；某个节点在 norm 上没有命中时，再在 raw 上匹配一次。
本阶段覆盖正文、标题、图注、脚注、列表、图内文字；表格单元格与图内 OCR 文字在 P5 产生后加入。
References 部分默认不参与检索（scope.sections 显式包含 references 时才检索）。
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

import regex
from pydantic import BaseModel, Field

from drugpaper.domain.nodes import DocumentNode
from drugpaper.text.normalize import Normalized, normalize_detail

SEARCHABLE_TYPES = frozenset(
    {"paragraph", "heading", "caption", "footnote", "list", "figure_part", "formula"}
)
DEFAULT_EXCLUDED_SECTIONS = frozenset({"references"})
MAX_PATTERN_LEN = 500
REGEX_TIMEOUT_S = 1.0
MAX_SNIPPET = 400
_SENT_SPLIT = regex.compile(r"(?<=[.!?;])\s+(?=[A-Z(\[0-9•])")


class GrepScope(BaseModel):
    pages: list[int] | None = None
    sections: list[str] | None = None
    node_types: list[str] | None = None


class GrepQuery(BaseModel):
    pattern: str = Field(min_length=1, max_length=MAX_PATTERN_LEN)
    literal: bool = False
    case_sensitive: bool = False
    scope: GrepScope | None = None
    context_lines: int = Field(default=1, ge=0, le=5)
    max_results: int = Field(default=20, ge=1, le=200)


class GrepMatch(BaseModel):
    node_id: str  # 全局节点引用
    local_id: str
    page: int | None
    section: str | None
    node_type: str
    snippet: str  # 命中句及上下文（text_norm）
    char_span: tuple[int, int]  # text_raw 坐标，可直接作为证据
    match: str  # 命中的原文片段


class GrepResult(BaseModel):
    matches: list[GrepMatch] = Field(default_factory=list)
    truncated: bool = False
    total: int = 0
    error: str | None = None


class GrepError(ValueError):
    pass


@dataclass
class _Entry:
    node: DocumentNode
    detail: Normalized


def compile_pattern(q: GrepQuery) -> regex.Pattern[str]:
    flags = regex.V0 | (0 if q.case_sensitive else regex.IGNORECASE)
    pat = regex.escape(q.pattern) if q.literal else q.pattern
    try:
        return regex.compile(pat, flags)
    except regex.error as e:
        raise GrepError(f"正则无效：{e}（可以设置 literal=true 按字面匹配）") from e


def _sentence_bounds(text: str) -> list[tuple[int, int]]:
    bounds: list[tuple[int, int]] = []
    start = 0
    for m in _SENT_SPLIT.finditer(text):
        bounds.append((start, m.start()))
        start = m.end()
    bounds.append((start, len(text)))
    return bounds


def make_snippet(text: str, start: int, end: int, context: int) -> str:
    bounds = _sentence_bounds(text)
    i = next((k for k, (a, b) in enumerate(bounds) if a <= start <= b), 0)
    a = bounds[max(0, i - context)][0]
    b = bounds[min(len(bounds) - 1, i + context)][1]
    if b - a > MAX_SNIPPET:  # 句子太长：以命中处为中心截取
        half = (MAX_SNIPPET - (end - start)) // 2
        a2, b2 = max(a, start - max(half, 40)), min(b, end + max(half, 40))
        return ("…" if a2 > 0 else "") + text[a2:b2] + ("…" if b2 < len(text) else "")
    return text[a:b]


class GrepEngine:
    """按 (document_id, revision) 建一次，缓存所有可检索节点的规范化结果（节点更新时重建）。"""

    def __init__(self, nodes: Iterable[DocumentNode]) -> None:
        entries = [
            n for n in nodes if n.node_type in SEARCHABLE_TYPES and n.text_raw and n.status == "active"
        ]
        entries.sort(key=lambda n: n.reading_order)
        self._entries = [_Entry(n, normalize_detail(n.text_raw or "")) for n in entries]

    def __len__(self) -> int:
        return len(self._entries)

    def _in_scope(self, n: DocumentNode, scope: GrepScope | None) -> bool:
        if scope is None or scope.sections is None:
            if n.section_role in DEFAULT_EXCLUDED_SECTIONS:
                return False
        elif n.section_role not in scope.sections:
            return False
        if scope is None:
            return True
        if scope.node_types is not None and n.node_type not in scope.node_types:
            return False
        return scope.pages is None or bool(set(n.geometry.pages()) & set(scope.pages))

    def grep(self, q: GrepQuery) -> GrepResult:
        pat = compile_pattern(q)
        result = GrepResult()
        try:
            for e in self._entries:
                if not self._in_scope(e.node, q.scope):
                    continue
                d = e.detail
                found = list(pat.finditer(d.norm, timeout=REGEX_TIMEOUT_S))
                on_raw = False
                if not found:
                    found = list(pat.finditer(d.raw, timeout=REGEX_TIMEOUT_S))
                    on_raw = True
                for m in found:
                    if m.end() == m.start():
                        continue
                    result.total += 1
                    if len(result.matches) >= q.max_results:
                        result.truncated = True
                        continue
                    if on_raw:
                        span = (m.start(), m.end())
                        snippet = make_snippet(d.raw, m.start(), m.end(), q.context_lines)
                    else:
                        span = d.map_span(m.start(), m.end())
                        snippet = make_snippet(d.norm, m.start(), m.end(), q.context_lines)
                    result.matches.append(
                        GrepMatch(
                            node_id=e.node.ref,
                            local_id=e.node.local_id,
                            page=e.node.page,
                            section=e.node.section_role,
                            node_type=e.node.node_type,
                            snippet=snippet,
                            char_span=span,
                            match=d.raw[span[0] : span[1]],
                        )
                    )
        except TimeoutError:
            result.error = f"正则匹配超时（>{REGEX_TIMEOUT_S}s），请简化模式"
            result.truncated = True
        return result
