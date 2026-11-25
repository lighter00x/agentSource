"""DocumentSource 的 IR 实现（执行方案 S2.10），替换 P1 的 LayoutJsonSource。

- 实现 P1 冻结的 `DocumentSource` 协议（blocks / block / search_text / pages / document_id）；
- 额外提供 `outline()`、`node(ref)`、`children(ref)`、`window(ref, before, after)`、`section(key)`、
  `page_nodes(page)`、`grep(query)`，供 doc_read / search 工具包使用；
- 引用一律用节点全局引用 `{document_id}/r{rev}/{local_id}`，也接受局部 ID。
"""

from __future__ import annotations

from functools import cached_property
from typing import Any

from drugpaper.docsrc.base import BlockView, Hit
from drugpaper.domain import ids
from drugpaper.domain.nodes import VISUAL_TYPES, DocumentNode, IRDocument
from drugpaper.persistence.db import Database
from drugpaper.persistence.repos.nodes import load_ir
from drugpaper.text.grep import GrepEngine, GrepQuery, GrepResult

STRUCTURAL = frozenset({"document", "front_matter", "section"})


class IrDocumentSource:
    def __init__(self, ir: IRDocument) -> None:
        self.ir = ir
        self._by_id = {n.local_id: n for n in ir.nodes}

    @classmethod
    async def load(cls, db: Database, document_id: str, revision: int | None = None) -> IrDocumentSource:
        async with db.session() as s:
            return cls(await load_ir(s, document_id, revision))

    # ------------------------------------------------------------ 基础

    @property
    def document_id(self) -> str:
        return self.ir.document_id

    @property
    def revision(self) -> int:
        return self.ir.revision

    def ref(self, local_id: str) -> str:
        return ids.global_ref(self.document_id, self.revision, local_id)

    def local_id(self, ref: str) -> str:
        parsed = ids.parse_ref(ref)
        if parsed is None:
            return ref
        doc, rev, local = parsed
        if doc != self.document_id or rev != self.revision:
            raise KeyError(f"引用不属于当前文档：{ref}")
        return local

    def node(self, ref: str) -> DocumentNode:
        try:
            return self._by_id[self.local_id(ref)]
        except KeyError:
            raise KeyError(f"节点不存在：{ref}") from None

    def text_of(self, n: DocumentNode, *, norm: bool = True) -> str:
        """节点可读文本；视觉节点取其图注与脚注。"""
        own = (n.text_norm if norm else n.text_raw) or ""
        if own or n.node_type not in VISUAL_TYPES:
            return own
        parts = [
            (c.text_norm if norm else c.text_raw) or ""
            for c in (self._by_id[k] for k in n.children if k in self._by_id)
            if c.node_type in ("caption", "footnote")
        ]
        return "\n".join(p for p in parts if p)

    # ------------------------------------------------------------ DocumentSource 协议（P1）

    def pages(self) -> list[int]:
        return [p.page for p in self.ir.pages] or sorted(
            {n.page for n in self.ir.nodes if n.page is not None}
        )

    def _view(self, n: DocumentNode) -> BlockView:
        bbox = n.geometry.regions[0].bbox.as_tuple() if n.geometry.regions else None
        return BlockView(
            id=self.ref(n.local_id),
            page=n.page if n.page is not None else -1,
            type=n.node_type,
            kind="group" if n.node_type in VISUAL_TYPES else "block",
            text=self.text_of(n, norm=False),
            bbox=bbox,
            members=[self.ref(c) for c in n.children],
        )

    @cached_property
    def _content(self) -> list[DocumentNode]:
        return [n for n in self.ir.nodes if n.node_type not in STRUCTURAL and n.node_type != "discarded"]

    def blocks(self, page: int | None = None) -> list[BlockView]:
        nodes = self._content if page is None else self.page_nodes(page)
        return [self._view(n) for n in nodes]

    def block(self, block_id: str) -> BlockView:
        return self._view(self.node(block_id))

    @cached_property
    def grep_engine(self) -> GrepEngine:
        return GrepEngine(self.ir.nodes)

    def search_text(self, pattern: str, max_results: int = 10) -> list[Hit]:
        res = self.grep_engine.grep(GrepQuery(pattern=pattern, literal=False, max_results=max_results))
        return [
            Hit(block_id=m.node_id, page=m.page or 0, type=m.node_type, snippet=m.snippet)
            for m in res.matches
        ]

    # ------------------------------------------------------------ 扩展查询

    def grep(self, q: GrepQuery) -> GrepResult:
        return self.grep_engine.grep(q)

    def children(self, ref: str) -> list[DocumentNode]:
        n = self.node(ref)
        return [self._by_id[c] for c in n.children if c in self._by_id]

    def window(self, ref: str, before: int = 1, after: int = 1) -> list[DocumentNode]:
        n = self.node(ref)
        if n.parent_id is None or n.parent_id not in self._by_id:
            return [n]
        sibs = self.children(n.parent_id)
        i = next(k for k, s in enumerate(sibs) if s.local_id == n.local_id)
        return sibs[max(0, i - before) : i + after + 1]

    def page_nodes(self, page: int) -> list[DocumentNode]:
        return [n for n in self._content if page in n.geometry.pages()]

    def sections(self) -> list[DocumentNode]:
        root = self._by_id.get(ids.ROOT_ID)
        if root is None:
            return []
        return [self._by_id[c] for c in root.children if c in self._by_id]

    def section(self, key: str) -> DocumentNode:
        """按章节 ID（s002）、全局引用、section_role（results）或标题（RESULTS AND DISCUSSION）查找。"""
        k = key.strip()
        for s in self.sections():
            if k in (s.local_id, self.ref(s.local_id)) or k.lower() == (s.section_role or "").lower():
                return s
            if s.label and k.upper() == s.label.upper():
                return s
        raise KeyError(f"章节不存在：{key}")

    def descendants(self, n: DocumentNode) -> list[DocumentNode]:
        out: list[DocumentNode] = []
        for c in n.children:
            if c in self._by_id:
                child = self._by_id[c]
                out.append(child)
                out.extend(self.descendants(child))
        return out

    def outline(self) -> dict[str, Any]:
        sections: list[dict[str, Any]] = []
        for s in self.sections():
            headings = [
                {"id": self.ref(c.local_id), "text": (c.text_norm or "")[:60], "page": c.page}
                for c in self.descendants(s)
                if c.node_type == "heading" and c.attributes.get("level", 1) >= 2
            ]
            sections.append(
                {
                    "id": self.ref(s.local_id),
                    "label": s.label or ("FRONT MATTER" if s.node_type == "front_matter" else None),
                    "role": s.section_role,
                    "page": s.page
                    if s.page is not None
                    else next((c.page for c in self.descendants(s) if c.page is not None), None),
                    "subheadings": headings,
                }
            )
        visuals = [
            {
                "id": self.ref(n.local_id),
                "type": n.node_type,
                "label": n.label,
                "subtype": n.subtype,
                "page": n.page,
            }
            for n in self._content
            if n.node_type in VISUAL_TYPES
        ]
        return {
            "document_id": self.document_id,
            "title": self.ir.meta.title,
            "doi": self.ir.meta.doi,
            "pages": len(self.pages()),
            "sections": sections,
            "figures_tables": visuals,
        }
