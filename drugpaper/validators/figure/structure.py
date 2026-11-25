from __future__ import annotations

from typing import Any

from drugpaper.validators.figure.codes import CheckResult, issue


def references(value: Any) -> set[int]:
    found = set()
    if isinstance(value, dict):
        for key, item in value.items():
            if key in {"mol_id", "scaffold_mol_id"} and type(item) is int:
                found.add(item)
            elif key == "molecule_inventory":
                continue
            elif key == "mol_ids" and isinstance(item, list):
                found.update(i for i in item if type(i) is int)
            elif key == "substituents" and isinstance(item, dict):
                found.update(v for v in item.values() if isinstance(v, int))
            else:
                found.update(references(item))
    elif isinstance(value, list):
        for item in value:
            found.update(references(item))
    return found


def check(ctx: dict[str, Any]) -> list[CheckResult]:
    out = []
    data = ctx.get("extraction", {})
    boxes = ctx.get("boxes", [])
    structures = ctx.get("structures", [])
    image_type = ctx.get("image_type", "other")
    prior = ctx.get("prior_type")
    if (
        prior
        and prior not in {"figure", image_type}
        and not (prior == "scheme" and image_type.startswith("synthetic_scheme"))
        and not (prior == "table" and image_type in {"sar_table", "data_table"})
    ):
        out.append(issue("C_CLASS_PRIOR"))
    referenced_3d = (
        image_type == "molecular_docking"
        and ctx.get("reference_3d_complete")
        and bool(ctx.get("reference_bindings"))
        and not ctx.get("backfill_errors")
    )
    if ctx.get("expects_structures") and not boxes and not referenced_3d:
        missing = issue("C_NO_BOX")
        missing.severity = "error"
        out.append(missing)
    if (
        structures
        and sum(not s.get("validation", {}).get("valid") for s in structures) / len(structures) > 0.3
    ):
        out.append(issue("C_OCSR_FAIL"))
    ids = {b["mol_id"] for b in boxes}
    used = references(data)
    excluded = {
        i.get("mol_id")
        for i in data.get("molecule_inventory", [])
        if i.get("disposition") == "excluded" and i.get("evidence_text") and type(i.get("mol_id")) is int
    }
    if ids and ids - used - excluded:
        out.append(issue("C_UNREF_BOX"))
    if used - ids:
        out.append(issue("C_BACKFILL_ERR", "mol_id", "reference to nonexistent box"))
    if ctx.get("backfill_errors"):
        failed = issue("C_BACKFILL_ERR")
        if all(
            isinstance(e, dict) and e.get("code") == "markush_candidate_unresolved"
            for e in ctx["backfill_errors"]
        ):
            failed.message = "部分Markush候选无法拼接，需核对取代基定义或连接位点，并非分子框编号缺失。"
        failed.severity = "error"
        out.append(failed)
    labels = [m.get("reference") for m in data.get("molecules", []) if m.get("reference")]
    known = set(ctx.get("known_labels", []))
    if known and labels and len(set(labels) - known) / len(set(labels)) > 0.5:
        out.append(issue("C_LABEL_UNKNOWN"))
    return out
