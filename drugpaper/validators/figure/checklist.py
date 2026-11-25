from __future__ import annotations

import json
from typing import Any

from drugpaper.capabilities.tokens import estimate_tokens
from drugpaper.facts.figure_models import ReviewResult
from drugpaper.validators.figure.codes import CheckResult
from drugpaper.validators.figure.numeric import text_match


def build(
    ctx: dict[str, Any], checks: list[CheckResult], budget: int = 1800
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    candidates: list[dict[str, Any]] = [
        {
            "dimension": c.dimension,
            "field_path": c.field_path or c.code,
            "code": c.code,
            "expected": c.message,
        }
        for c in checks
    ]
    for m in ctx.get("measurements", []):
        if text_match(m["value_raw"], ctx.get("text_tokens", [])):
            continue
        candidates.append(
            {"dimension": "numeric", "field_path": m["evidence"][0]["field_path"], "expected": m["value_raw"]}
        )
    data = ctx.get("extraction", {})
    if "header_rows" in data and "rows" in data:
        for dimension, path in [
            ("chart", "header_rows"),
            ("content", "footnote_markers"),
            ("content", "orientation"),
            ("structure", "scaffold_mol_id"),
        ]:
            if path in data:
                candidates.append({"dimension": dimension, "field_path": path, "expected": data[path]})
        if data.get("orientation") == "properties_as_rows":
            candidates.append(
                {
                    "dimension": "content",
                    "field_path": "subject_compound_label",
                    "expected": data.get("subject_compound_label"),
                }
            )
        candidates.append(
            {
                "dimension": "chart",
                "field_path": "rows",
                "expected": {"row_count": len(data["rows"]), "column_counts": [len(r) for r in data["rows"]]},
                "focus": "Verify all printed data rows and columns are present; do not certify only the existing rows.",
            }
        )
    if ctx.get("molecular_screened") or ctx.get("boxes") or ctx.get("expects_structures"):
        candidates.insert(
            0,
            {
                "dimension": "structure",
                "field_path": "molecule_inventory",
                "expected": {
                    "boxes": ctx.get("boxes", []),
                    "dispositions": data.get("molecule_inventory", []),
                    "document_reference_bindings": ctx.get("reference_bindings", []),
                },
                "focus": "Inspect the ORIGINAL image for missed 2D chemical drawings, verify every box ID and field binding, and confirm nonchemical exclusions. For 3D docking sticks verify printed ligand names against document_reference_bindings; repeated views of one ligand share its reference. source_ref is the external node supplying the chemical graph, not the current caption; never rewrite it to this figure. No invented 2D box is needed for 3D sticks. Missing/ambiguous references remain incorrect/unsure.",
            },
        )
    for i, m in enumerate(data.get("molecules", [])):
        candidates.append(
            {
                "dimension": "structure",
                "field_path": f"molecules[{i}].reference",
                "expected": m.get("reference"),
            }
        )
    for i, step in enumerate(data.get("optimization_steps", [])):
        candidates.append({"dimension": "chart", "field_path": f"optimization_steps[{i}]", "expected": step})
    if data.get("x_axis"):
        candidates.append({"dimension": "chart", "field_path": "x_axis", "expected": data["x_axis"]})
        candidates.append({"dimension": "chart", "field_path": "y_axis", "expected": data["y_axis"]})
    if data.get("description"):
        candidates.append(
            {"dimension": "content", "field_path": "description", "expected": data["description"]}
        )
    selected = []
    unchecked = []
    used = 0
    seen = set()
    for item in candidates:
        identity = (item["dimension"], item["field_path"])
        if identity in seen:
            continue
        seen.add(identity)
        cost = estimate_tokens(json.dumps(item, ensure_ascii=False, default=str))
        if used + cost > budget:
            unchecked.append(item)
        else:
            selected.append(item)
            used += cost
    return selected, unchecked


def passed(result: ReviewResult, requested: list[dict[str, Any]] | None = None) -> bool:
    if requested is not None:
        wanted = {(i["dimension"], i["field_path"]) for i in requested}
        actual = {(i.dimension, i.field_path) for i in result.items}
        if not wanted.issubset(actual):
            return False
    return (
        not any(i.verdict == "incorrect" for i in result.items)
        and sum(i.verdict == "unsure" for i in result.items) / max(len(result.items), 1) <= 0.1
    )


def review_material(
    data: dict[str, Any], checklist: list[dict[str, Any]], max_chars: int = 24000
) -> dict[str, Any]:
    """Keep large grids/curves out of the prompt; closed paths still refer to originals."""
    import copy

    from drugpaper.agents.figure.remediation import locate

    if len(json.dumps(data, ensure_ascii=False, default=str)) <= max_chars:
        return data
    out: dict[str, Any] = {
        k: copy.deepcopy(data[k])
        for k in ("status", "title", "orientation", "subject_compound_label", "description")
        if k in data
    }
    out["field_counts"] = {k: len(v) for k, v in data.items() if isinstance(v, list)}
    values = {}
    for item in checklist:
        path = item["field_path"]
        try:
            parent, key = locate(data, path)
            value = parent[key]
        except (KeyError, IndexError, ValueError, TypeError):
            continue
        if len(json.dumps(value, ensure_ascii=False, default=str)) > 2500:
            value = {"count": len(value) if isinstance(value, (list, dict)) else None, "summary_only": True}
        values[path] = copy.deepcopy(value)
    out["requested_field_values"] = values
    out["material_note"] = (
        "Only the requested closed paths are presented. field_counts refer to the original extraction. Unpresented values remain unchecked; do not certify them."
    )
    return out
