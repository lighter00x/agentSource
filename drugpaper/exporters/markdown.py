from __future__ import annotations

import json
from typing import Any

from drugpaper.facts.full_text import FullTextContext


def figure_description(report: dict[str, Any]) -> str:
    extraction = report.get("extraction", {})
    if extraction.get("description"):
        return str(extraction["description"])
    # Structured facts are formatted deterministically when figure_describe is disabled.
    fields = {
        key: extraction[key]
        for key in (
            "title",
            "header_rows",
            "rows",
            "molecules",
            "optimization_steps",
            "pathways",
            "series",
            "key_findings",
        )
        if extraction.get(key)
    }
    return "图像结构化结果：\n\n```json\n" + json.dumps(fields, ensure_ascii=False, indent=2) + "\n```"


def corpus_markdown(ctx: FullTextContext, image_paths: dict[str, str]) -> str:
    parts = [f"# {ctx.ir.meta.title or ctx.ir.document_id}"]
    for n in sorted(ctx.ir.nodes, key=lambda n: (n.reading_order, n.local_id)):
        if n.status == "removed" or n.node_type in {"document", "front_matter", "discarded"}:
            continue
        if n.ref in image_paths:
            parts.append(f"![{n.label or n.node_type}]({image_paths[n.ref]})")
            # Deliberately the immediately following block, matching legacy insert_image_info.
            report = ctx.reports.get(n.ref)
            parts.append(figure_description(report) if report else "图像尚无结构化结果，需复核。")
        elif n.text_raw:
            parts.append(("## " if n.node_type in {"section", "heading"} else "") + n.text_raw)
    return "\n\n".join(parts) + "\n"


def audit_markdown(ctx: FullTextContext, result: dict[str, Any]) -> str:
    parts = [
        f"# 审计：{ctx.ir.document_id}",
        f"Revision: {ctx.ir.revision}\n\nFingerprint: {ctx.fingerprint}",
        "## 校验和告警",
        "```json\n"
        + json.dumps(
            {
                "checks": result.get("checks", []),
                "warnings": result.get("projection", {})
                .get("audit", {})
                .get("_audit", {})
                .get("warnings", []),
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n```",
        "## 节点与可回跳区域",
    ]
    for n in ctx.ir.nodes:
        if n.status != "removed":
            parts.append(
                f"### {n.ref}\n\n页码（从0开始）：{n.page}\n\n```json\n{n.geometry.model_dump_json()}\n```\n\n{n.text_raw or n.label or ''}"
            )
    parts.extend(
        [
            "## 产物版本",
            "```json\n" + json.dumps(ctx.artifacts, ensure_ascii=False, indent=2) + "\n```",
            "## 路径推理与证据",
            "```json\n" + json.dumps(result.get("history", []), ensure_ascii=False, indent=2) + "\n```",
        ]
    )
    return "\n\n".join(parts) + "\n"
