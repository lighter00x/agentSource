from __future__ import annotations

import re
from typing import Any

from drugpaper.agents.lookup.models import LookupAction, LookupQuestion, LookupResult
from drugpaper.domain.nodes import EvidenceRef
from drugpaper.facts.full_text import FullTextContext
from drugpaper.facts.metric_dictionary import MetricDictionary
from drugpaper.tools.protocol import ToolResult


def fast_action(q: LookupQuestion, ctx: FullTextContext, index: int) -> LookupAction | None:
    if q.kind == "vision":
        return None
    label = re.search(r"(?:compound\s*|化合物\s*)?(\d+[a-z]?|UCM-\d+)", q.question, re.I)
    if label and re.search(r"出现在哪|出现位置|where.*appear", q.question, re.I):
        return LookupAction(
            question_id=index,
            tool="grep",
            args={"pattern": rf"(?<![A-Za-z0-9]){re.escape(label[1])}(?![A-Za-z0-9])", "max_results": 20},
        )
    if label and re.search(r"值|value", q.question, re.I):
        metric_text = re.sub(
            r"(?:化合物|compound)?\s*" + re.escape(label[1]), "", q.question, flags=re.I
        ).strip()
        metric_text = re.sub(r"^[的\s]+|(?:的?值(?:是多少)?|value)\s*[？?]?\s*$", "", metric_text, flags=re.I)
        metric = MetricDictionary().match(metric_text)
        if not metric["id"].startswith("unmapped:"):
            return LookupAction(
                question_id=index,
                tool="query_measurements",
                args={"subject_label": label[1], "metric": metric["id"]},
            )
    figure = re.search(r"(Figure|Fig\.?|Table|Scheme|图|表)\s*(\d+)", q.question, re.I)
    if figure and re.search(r"图注|caption", q.question, re.I):
        prefix = {"图": "figure", "表": "table", "fig": "figure", "fig.": "figure"}.get(
            figure[1].lower(), figure[1].lower()
        )
        matches = [
            n
            for n in ctx.ir.nodes
            if n.status != "removed"
            and n.node_type == "caption"
            and re.search(rf"\b{prefix}\s*{figure[2]}(?!\d)", n.text_raw or n.label or "", re.I)
        ]
        if len(matches) == 1:
            return LookupAction(question_id=index, tool="get_node", args={"node_id": matches[0].ref})
    return None


def format_fast(q: LookupQuestion, action: LookupAction, result: ToolResult) -> LookupResult:
    data: dict[str, Any] = result.data or {}
    evidence: list[EvidenceRef] = []
    answer = ""
    pages: list[int] = []
    if result.ok and action.tool == "grep":
        for m in data.get("matches", []):
            evidence.append(EvidenceRef(node_id=m["node_id"], char_span=m.get("char_span")))
            if m.get("page") is not None:
                pages.append(m["page"])
        answer = (
            "出现于页码（从0开始）：" + "、".join(map(str, sorted(set(pages))))
            if evidence
            else "未找到该编号。"
        )
    elif result.ok and action.tool == "query_measurements":
        values = data.get("measurements", [])
        answer = "；".join(
            f"{m['metric_label_raw']}：{m['value_raw']} {m.get('unit_raw') or ''}".strip() for m in values
        )
        evidence = [EvidenceRef.model_validate(e) for m in values for e in m.get("evidence", [])]
    elif result.ok and action.tool == "get_node":
        answer = data.get("text", "")
        evidence = [EvidenceRef(node_id=r) for r in result.refs]
    return LookupResult(
        question=q.question,
        answer=answer[:150],
        status="found" if evidence else "not_found",
        evidence=evidence,
        suggested_pages=sorted(set(pages)),
    )
