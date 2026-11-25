"""3 个只读文档工具：list_blocks / get_block / grep（P2 替换为 IR 版）。

每个工具都把返回的块 ID 写入 ToolResult.refs，供引用校验使用。
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from drugpaper.docsrc.base import DocumentSource
from drugpaper.tools.protocol import Tool, ToolContext, ToolResult, tool

PREVIEW_CHARS = 80


class ListBlocksIn(BaseModel):
    page: int = Field(ge=0, description="页码，从 0 开始")


class GetBlockIn(BaseModel):
    block_id: str = Field(description="块 ID，例如 p000.b007 或 p003.g000")


class GrepIn(BaseModel):
    pattern: str = Field(min_length=1, description="正则或关键词，大小写不敏感")
    max_results: int = Field(default=10, ge=1, le=50)


def _preview(text: str) -> str:
    t = text.replace("\n", " ")
    return t if len(t) <= PREVIEW_CHARS else t[:PREVIEW_CHARS] + "…"


def make_layout_tools(source: DocumentSource) -> list[Tool]:
    @tool("list_blocks", "列出某一页的块：ID、类型、前 80 字。")
    async def list_blocks(ctx: ToolContext, args: ListBlocksIn) -> ToolResult:
        pages = source.pages()
        if args.page not in pages:
            rng = f"{pages[0]}~{pages[-1]}" if pages else "无"
            return ToolResult(ok=False, error=f"页码 {args.page} 不存在，可用页码：{rng}")
        blocks = source.blocks(args.page)
        return ToolResult(
            ok=True,
            data={
                "page": args.page,
                "blocks": [{"id": b.id, "type": b.type, "preview": _preview(b.text)} for b in blocks],
            },
            refs=[b.id for b in blocks],
        )

    @tool("get_block", "读取一个块的全文、类型、页码和归一化 bbox。")
    async def get_block(ctx: ToolContext, args: GetBlockIn) -> ToolResult:
        try:
            b = source.block(args.block_id)
        except KeyError:
            return ToolResult(ok=False, error=f"块不存在：{args.block_id}")
        data = {"id": b.id, "type": b.type, "page": b.page, "text": b.text, "bbox": b.bbox}
        if b.members:
            data["members"] = b.members
        return ToolResult(ok=True, data=data, refs=[b.id])

    @tool("grep", "在全文中按正则或关键词检索（大小写不敏感），返回块 ID、页码和片段。")
    async def grep(ctx: ToolContext, args: GrepIn) -> ToolResult:
        hits = source.search_text(args.pattern, args.max_results)
        return ToolResult(
            ok=True,
            data={"count": len(hits), "hits": [h.model_dump() for h in hits]},
            refs=[h.block_id for h in hits],
        )

    return [list_blocks, get_block, grep]


LAYOUT_TOOL_NAMES = ["list_blocks", "get_block", "grep"]
