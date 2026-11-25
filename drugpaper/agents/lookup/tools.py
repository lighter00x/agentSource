from __future__ import annotations

from pydantic import BaseModel

from drugpaper.docsrc.ir_source import IrDocumentSource
from drugpaper.facts.full_text import FullTextContext
from drugpaper.tools.builtin.doc_read import make_doc_read_tools
from drugpaper.tools.builtin.search import make_search_tools
from drugpaper.tools.protocol import ToolContext, ToolResult, tool
from drugpaper.tools.registry import ToolRegistry

READ_ONLY = frozenset(
    {
        "grep",
        "search",
        "get_node",
        "get_context_window",
        "get_page_text",
        "list_compounds",
        "get_compound",
        "query_measurements",
        "get_figure_report",
        "get_table",
    }
)


class FactQuery(BaseModel):
    subject_label: str | None = None
    metric: str | None = None
    compound_id: str | None = None
    query: str = ""
    node_ref: str | None = None


def lookup_tools(ctx: FullTextContext) -> ToolRegistry:
    registry = ToolRegistry(
        [*make_doc_read_tools(IrDocumentSource(ctx.ir)), *make_search_tools(IrDocumentSource(ctx.ir))]
    )

    @tool("query_measurements", "只读查询当前测定；按subject_label、metric筛选，保留条件和证据。")
    async def measurements(tc: ToolContext, args: FactQuery) -> ToolResult:
        aliases = {
            c["primary_label"]
            for c in ctx.compounds
            if args.subject_label in [c["primary_label"], *c.get("aliases", [])]
        }
        rows = [
            m
            for m in ctx.measurements
            if (
                args.subject_label is None
                or m.get("subject_label") == args.subject_label
                or m.get("subject_label") in aliases
            )
            and (args.metric is None or m["metric"] == args.metric)
        ]
        assays = {a["assay_id"]: a for a in ctx.assays}
        rows = [{**m, "conditions": assays.get(m.get("assay_id"), {}).get("conditions", {})} for m in rows]
        return ToolResult(
            ok=True,
            data={"measurements": rows[:30]},
            refs=list(dict.fromkeys(e["node_id"] for m in rows[:30] for e in m["evidence"])),
        )

    @tool("list_compounds", "只读检索化合物编号/别名；query可为空。")
    async def compounds(tc: ToolContext, args: FactQuery) -> ToolResult:
        rows = [
            c
            for c in ctx.compounds
            if args.query.lower() in " ".join([c["primary_label"], *c.get("aliases", [])]).lower()
        ]
        return ToolResult(
            ok=True,
            data={"compounds": rows[:20]},
            refs=[e["node_id"] for c in rows[:20] for e in c.get("evidence", [])],
        )

    @tool("get_compound", "只读化合物档案；compound_id或subject_label。")
    async def compound(tc: ToolContext, args: FactQuery) -> ToolResult:
        rows = [
            c
            for c in ctx.compounds
            if c["compound_id"] == args.compound_id
            or args.subject_label in [c["primary_label"], *c.get("aliases", [])]
        ]
        return ToolResult(
            ok=True,
            data={"compounds": rows},
            refs=[e["node_id"] for c in rows for e in c.get("evidence", [])],
        )

    @tool("get_figure_report", "只读已提交FigureReport；没有报告直接返回pending，不等待。")
    async def report(tc: ToolContext, args: FactQuery) -> ToolResult:
        row = ctx.reports.get(args.node_ref or "")
        return ToolResult(
            ok=True,
            data={"report": row} if row else {"status": "pending"},
            refs=[args.node_ref] if row and args.node_ref else [],
        )

    @tool("get_table", "只读表格结构化结果；node_ref必须为当前文档节点。")
    async def table(tc: ToolContext, args: FactQuery) -> ToolResult:
        row = ctx.reports.get(args.node_ref or "")
        return ToolResult(
            ok=True,
            data={"table": row.get("extraction", {})} if row else {"status": "pending"},
            refs=[args.node_ref] if row and args.node_ref else [],
        )

    for t in (measurements, compounds, compound, report, table):
        registry.register(t)
    return registry
