"""IR 版文档读取工具包（执行方案 S2.10）：get_outline / get_node / get_children / get_context_window / get_section / get_page_text。

返回的文本是 `text_norm`（LaTeX 已转写，便于模型阅读）；`ToolResult.refs` 是节点全局引用。
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from drugpaper.docsrc.ir_source import IrDocumentSource
from drugpaper.domain.nodes import DocumentNode
from drugpaper.tools.protocol import Tool, ToolContext, ToolResult, tool

NODE_TEXT_LIMIT = 3000
PREVIEW = 100


class Empty(BaseModel):
    pass


class NodeIn(BaseModel):
    node_id: str = Field(
        description="节点引用（工具结果中的 id 字段），如 doc_.../r1/p000.b007；也接受局部 ID p000.b007"
    )


class WindowIn(BaseModel):
    node_id: str = Field(description="节点引用")
    before: int = Field(default=1, ge=0, le=5, description="向前取几个兄弟节点")
    after: int = Field(default=1, ge=0, le=5, description="向后取几个兄弟节点")


class SectionIn(BaseModel):
    section: str = Field(
        description="章节：role（results / experimental …）、章节 id 或标题（RESULTS AND DISCUSSION）"
    )
    max_chars: int = Field(default=6000, ge=500, le=20000)


class PageIn(BaseModel):
    page: int = Field(ge=0, description="页码，从 0 开始")
    max_chars: int = Field(default=6000, ge=500, le=20000)


def _brief(src: IrDocumentSource, n: DocumentNode) -> dict[str, Any]:
    text = src.text_of(n)
    d: dict[str, Any] = {"id": src.ref(n.local_id), "type": n.node_type, "page": n.page}
    if n.label:
        d["label"] = n.label
    if n.subtype:
        d["subtype"] = n.subtype
    if text:
        d["preview"] = text if len(text) <= PREVIEW else text[:PREVIEW] + "…"
    return d


def _full(src: IrDocumentSource, n: DocumentNode, limit: int = NODE_TEXT_LIMIT) -> dict[str, Any]:
    text = src.text_of(n)
    d: dict[str, Any] = {
        "id": src.ref(n.local_id),
        "type": n.node_type,
        "page": n.page,
        "section": n.section_role,
        "text": text if len(text) <= limit else text[:limit] + "…（节点文本过长已截断）",
        "regions": [
            {"page": r.page, "bbox": [round(v, 4) for v in r.bbox.as_tuple()], "role": r.role}
            for r in n.geometry.regions
        ],
    }
    if n.label:
        d["label"] = n.label
    if n.subtype:
        d["subtype"] = n.subtype
    if n.parent_id:
        d["parent"] = src.ref(n.parent_id)
    if n.children:
        d["children"] = [src.ref(c) for c in n.children]
    if n.attributes.get("footnote_marks"):
        d["footnote_marks"] = n.attributes["footnote_marks"]
    if n.node_type in ("figure", "table", "scheme"):
        d["note"] = "图表内容本阶段只有图片（P5 起提供结构化内容），可依据图注与正文作答"
    return d


def _collect(
    src: IrDocumentSource, nodes: list[DocumentNode], max_chars: int
) -> tuple[list[dict[str, Any]], bool]:
    out: list[dict[str, Any]] = []
    used = 0
    for n in nodes:
        text = src.text_of(n)
        if not text or n.node_type in ("section", "front_matter"):
            continue
        if used + len(text) > max_chars and out:
            return out, True
        out.append(
            {"id": src.ref(n.local_id), "type": n.node_type, "page": n.page, "text": text[: max_chars - used]}
        )
        used += len(text)
    return out, False


def make_doc_read_tools(src: IrDocumentSource) -> list[Tool]:
    @tool(
        "get_outline",
        "文档大纲：标题、DOI、章节（含小标题）、所有图表的标签与页码。适合作为第一步。",
        max_output_chars=16000,
    )
    async def get_outline(ctx: ToolContext, args: Empty) -> ToolResult:
        o = src.outline()
        refs = [s["id"] for s in o["sections"]] + [v["id"] for v in o["figures_tables"]]
        refs += [h["id"] for s in o["sections"] for h in s["subheadings"]]
        return ToolResult(ok=True, data=o, refs=refs)

    @tool("get_node", "读取一个节点的全文（规范化文本）、类型、页码、区域、父子节点。")
    async def get_node(ctx: ToolContext, args: NodeIn) -> ToolResult:
        try:
            n = src.node(args.node_id)
        except KeyError as e:
            return ToolResult(ok=False, error=str(e).strip("'\""))
        return ToolResult(ok=True, data=_full(src, n), refs=[src.ref(n.local_id)])

    @tool("get_children", "列出节点的子节点（图注、脚注、图内部件、章节内容）及预览。")
    async def get_children(ctx: ToolContext, args: NodeIn) -> ToolResult:
        try:
            kids = src.children(args.node_id)
        except KeyError as e:
            return ToolResult(ok=False, error=str(e).strip("'\""))
        items = [_brief(src, k) for k in kids]
        return ToolResult(ok=True, data={"children": items}, refs=[i["id"] for i in items])

    @tool("get_context_window", "读取节点前后相邻的兄弟节点全文（同一章节内），用于补充上下文。")
    async def get_context_window(ctx: ToolContext, args: WindowIn) -> ToolResult:
        try:
            nodes = src.window(args.node_id, args.before, args.after)
        except KeyError as e:
            return ToolResult(ok=False, error=str(e).strip("'\""))
        items = [_full(src, n, 1500) for n in nodes]
        return ToolResult(ok=True, data={"nodes": items}, refs=[i["id"] for i in items])

    @tool("get_section", "按章节读取正文（按阅读顺序拼接，超出 max_chars 截断）。", max_output_chars=22000)
    async def get_section(ctx: ToolContext, args: SectionIn) -> ToolResult:
        try:
            sec = src.section(args.section)
        except KeyError as e:
            return ToolResult(ok=False, error=str(e).strip("'\"") + "；可先调用 get_outline 查看章节")
        items, truncated = _collect(src, src.descendants(sec), args.max_chars)
        data = {
            "section": src.ref(sec.local_id),
            "label": sec.label,
            "role": sec.section_role,
            "nodes": items,
        }
        if truncated:
            data["note"] = "章节内容超出 max_chars，已截断；可用 grep 定位后用 get_node 读取"
        return ToolResult(ok=True, data=data, refs=[i["id"] for i in items])

    @tool("get_page_text", "按页读取该页的全部节点文本（按阅读顺序）。", max_output_chars=22000)
    async def get_page_text(ctx: ToolContext, args: PageIn) -> ToolResult:
        if args.page not in src.pages():
            return ToolResult(
                ok=False, error=f"页码 {args.page} 不存在，共 {len(src.pages())} 页（从 0 开始）"
            )
        items, truncated = _collect(src, src.page_nodes(args.page), args.max_chars)
        data: dict[str, Any] = {"page": args.page, "nodes": items}
        if truncated:
            data["note"] = "该页内容超出 max_chars，已截断"
        return ToolResult(ok=True, data=data, refs=[i["id"] for i in items])

    return [get_outline, get_node, get_children, get_context_window, get_section, get_page_text]


DOC_READ_TOOL_NAMES = [
    "get_outline",
    "get_node",
    "get_children",
    "get_context_window",
    "get_section",
    "get_page_text",
]
