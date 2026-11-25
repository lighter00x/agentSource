from __future__ import annotations

import copy
import re
from typing import Any

from drugpaper.facts.figure_models import ChartDataset, ReviewResult, TableGrid, VisualExtraction
from drugpaper.skills.protocol import ArtifactDraft, SkillContext, SkillResult


def locate(payload: dict[str, Any], path: str) -> tuple[Any, str | int]:
    # Only traverse existing fields, reject new paths and prototype-like special names.
    parts = re.findall(r"([A-Za-z_][A-Za-z0-9_]*)|\[(\d+)\]", path)
    rebuilt = "".join(
        name if i == 0 else "." + name if name else "[" + idx + "]" for i, (name, idx) in enumerate(parts)
    )
    if rebuilt != path or "__" in path:
        raise ValueError("invalid correction field path")
    keys = [name if name else int(idx) for name, idx in parts]
    value: Any = payload
    for key in keys[:-1]:
        value = value[key]
    final = keys[-1]
    if isinstance(value, dict) and final not in value:
        raise ValueError("cannot create correction field")
    return value, final


def apply_review_fix(
    payload: dict[str, Any], review: ReviewResult, paths: list[str] | None = None
) -> dict[str, Any]:
    out = copy.deepcopy(payload)
    for item in review.items:
        if (
            item.verdict != "incorrect"
            or item.suggested_value is None
            or paths is not None
            and item.field_path not in paths
        ):
            continue
        parent, key = locate(out, item.field_path)
        if isinstance(parent, dict) and (parent.get("locked") or parent.get("source") == "manual"):
            raise PermissionError("manual field locked")
        current = parent[key]
        if isinstance(current, dict) and "text" in current and not isinstance(item.suggested_value, dict):
            if current.get("source") == "manual" or current.get("locked"):
                raise PermissionError("manual field locked")
            current["text"] = str(item.suggested_value)
            current["source"] = "vlm_review"
            continue
        parent[key] = item.suggested_value
        if isinstance(parent, dict):
            parent["source"] = "vlm_review"
    # Validate the whole candidate before the caller adopts it. A review may suggest
    # a scalar for a tick list or axis object; keep the previous usable extraction.
    if "x_axis" in payload and "y_axis" in payload:
        ChartDataset.model_validate(out)
    elif "header_rows" in payload and "rows" in payload:
        TableGrid.model_validate(out)
    else:
        VisualExtraction.model_validate(out)
    return out


def apply_text_layer(
    payload: dict[str, Any], paths: list[str], spans: list[dict[str, Any]]
) -> dict[str, Any]:
    out = copy.deepcopy(payload)
    for path in paths:
        row, column = map(int, re.findall(r"\[(\d+)\]", path))
        cell = out["rows"][row][column]
        box = cell.get("bbox")
        if not box:
            raise ValueError("cell missing bbox")
        if cell.get("source") == "manual" or cell.get("locked"):
            raise PermissionError("manual field locked")
        matches = [
            s
            for s in spans
            if box["x0"] <= (s["bbox"]["x0"] + s["bbox"]["x1"]) / 2 <= box["x1"]
            and box["y0"] <= (s["bbox"]["y0"] + s["bbox"]["y1"]) / 2 <= box["y1"]
        ]
        # Page and cell uniqueness must be established by the caller's scoped spans.
        if len(matches) != 1:
            raise ValueError("text layer is not uniquely matched")
        cell["text"] = matches[0]["text"]
        cell["source"] = "text_layer"
    return out


async def action_request(inputs: dict[str, Any], ctx: SkillContext) -> SkillResult:
    name = ctx.skill_id.split("@")[0]
    return SkillResult(
        artifacts=[
            ArtifactDraft(
                artifact_type="remediation_action",
                payload={
                    "action": name,
                    "params": inputs.get("params", {}),
                    "reason": inputs.get("reason", ""),
                },
            )
        ]
    )
