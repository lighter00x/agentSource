"""检索工具包（执行方案 S2.9）：grep（设计文档 7.9.3 节的输入输出）与 search（fts / trgm / hybrid）。"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from drugpaper.docsrc.ir_source import IrDocumentSource
from drugpaper.text.grep import GrepError, GrepQuery, GrepScope
from drugpaper.text.index import InMemorySearch, SearchBackend
from drugpaper.tools.protocol import Tool, ToolContext, ToolResult, tool


class GrepIn(BaseModel):
    pattern: str = Field(
        min_length=1, max_length=500, description="正则或字面量；如 \\b26\\b、UCM-1306、hERG"
    )
    literal: bool = Field(default=False, description="true 时按字面匹配（不解析正则）")
    case_sensitive: bool = False
    pages: list[int] | None = Field(default=None, description="限定页码（从 0 开始）")
    sections: list[str] | None = Field(
        default=None,
        description="限定章节 role：abstract/introduction/results/experimental/conclusion/references …",
    )
    node_types: list[str] | None = Field(
        default=None, description="限定节点类型：paragraph/caption/footnote/heading/list"
    )
    context_lines: int = Field(default=1, ge=0, le=5, description="命中句前后各带几句")
    max_results: int = Field(default=20, ge=1, le=100)


class SearchIn(BaseModel):
    query: str = Field(min_length=1, max_length=300, description="自然语言或关键词")
    mode: Literal["fts", "trgm", "hybrid"] = Field(
        default="hybrid", description="fts 全文 / trgm 模糊（容忍拼写）/ hybrid"
    )
    max_results: int = Field(default=8, ge=1, le=30)


def make_search_tools(src: IrDocumentSource, backend: SearchBackend | None = None) -> list[Tool]:
    search_backend: SearchBackend = backend or InMemorySearch(src.ir.nodes)

    @tool(
        "grep",
        "在全文（正文、标题、图注、脚注、列表）上精确检索，返回命中节点、页码、章节、命中句片段和原文位置。"
        "References 默认不检索。",
        max_output_chars=8000,
    )
    async def grep(ctx: ToolContext, args: GrepIn) -> ToolResult:
        scope = None
        if args.pages is not None or args.sections is not None or args.node_types is not None:
            scope = GrepScope(pages=args.pages, sections=args.sections, node_types=args.node_types)
        q = GrepQuery(
            pattern=args.pattern,
            literal=args.literal,
            case_sensitive=args.case_sensitive,
            scope=scope,
            context_lines=args.context_lines,
            max_results=args.max_results,
        )
        try:
            res = src.grep(q)
        except GrepError as e:
            return ToolResult(ok=False, error=str(e))
        matches = [
            {
                "node_id": m.node_id,
                "page": m.page,
                "section": m.section,
                "type": m.node_type,
                "snippet": m.snippet,
                "char_span": list(m.char_span),
            }
            for m in res.matches
        ]
        data: dict[str, object] = {"matches": matches, "truncated": res.truncated, "total": res.total}
        if res.error:
            data["error"] = res.error
        return ToolResult(ok=True, data=data, refs=list(dict.fromkeys(m.node_id for m in res.matches)))

    @tool("search", "按相关度检索（全文/模糊/混合），适合不确定确切写法时使用；精确定位请用 grep。")
    async def search(ctx: ToolContext, args: SearchIn) -> ToolResult:
        hits = await search_backend.search(args.query, args.mode, args.max_results)
        return ToolResult(
            ok=True,
            data={"hits": [h.model_dump(exclude={"local_id"}) for h in hits]},
            refs=[h.node_id for h in hits],
        )

    return [grep, search]


SEARCH_TOOL_NAMES = ["grep", "search"]
