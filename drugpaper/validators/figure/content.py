from __future__ import annotations

import re
from typing import Any

from drugpaper.validators.figure.codes import CheckResult, issue


def check(ctx: dict[str, Any]) -> list[CheckResult]:
    data = ctx.get("extraction", {})
    out = []
    if any(str(m.get("metric", "")).startswith("unmapped:") for m in ctx.get("measurements", [])):
        path = "footnote_markers" if "footnote_markers" in data else "y_axis.title"
        out.append(issue("C_METRIC_UNMAPPED", path))
    if data.get("status") in {"unclear", "not_a_table"}:
        out.append(issue("C_STATUS_UNCLEAR", "status"))
    required = {
        "sar_table": "rows",
        "data_table": "rows",
        "molecular_docking": "residues",
        "metabolic_pathway": "parent_drug",
        "pharmacophore_model": "features",
        "protac_architecture": "warhead",
    }.get(str(ctx.get("image_type", "")))
    if required and not data.get(required):
        out.append(issue("C_REQUIRED_EMPTY", required))
    source = " ".join(str(value or "") for value in ctx.get("source_text", []))
    description = data.get("description", "") + " " + " ".join(data.get("key_findings", []))
    entities = re.findall(r"\b(?:\d+[a-z]?|[A-Z][a-z]{2}\d+)\b", description)
    if source and any(entity not in source for entity in entities):
        out.append(issue("C_ENTITY_UNTRACEABLE", "description"))
    caption = str(ctx.get("caption") or "").lower()
    if ("pk profile" in caption or "time course" in caption) and not data.get("x_axis"):
        out.append(issue("C_CAPTION_MISMATCH"))
    return out
