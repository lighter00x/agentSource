"""保留旧十类 schema 行为，增加复数编号；原始对象不变。"""

from __future__ import annotations

import copy
from typing import Any

from drugpaper.facts.legacy_backfill import (
    BackfillContext,
    annotate_image_molecule_types,
    backfill_image_molecules,
)
from drugpaper.facts.legacy_molecule import Molecule


def backfill(
    node_ref: str, extraction_payload: dict[str, Any], molecules: dict[int, Molecule], image_type: int
) -> dict[str, Any]:
    result = copy.deepcopy(extraction_payload)
    index = {(node_ref, i): copy.deepcopy(m) for i, m in molecules.items()}
    context = BackfillContext(index, node_ref, image_type)
    annotate_image_molecule_types(result, image_type, index, node_ref)

    def plural(value: Any, path: str) -> None:
        if isinstance(value, list):
            for i, item in enumerate(value):
                plural(item, f"{path}[{i}]")
        elif isinstance(value, dict):
            if "mol_ids" in value:
                ids = value["mol_ids"]
                if not isinstance(ids, list):
                    context.record_error(path + ".mol_ids", ids, "invalid_mol_ids")
                else:
                    value["esmiles_list"] = [
                        context.resolve(i, f"{path}.mol_ids[{j}]") for j, i in enumerate(ids)
                    ]
            for key, child in list(value.items()):
                if key not in {"esmiles_list"}:
                    plural(child, f"{path}.{key}" if path else key)

    plural(result, "")
    # handlers 删除单个 mol_id，先记住复数引用，免于误报未引用。
    from drugpaper.facts.legacy_backfill import BACKFILL_HANDLERS

    BACKFILL_HANDLERS.get(image_type, BACKFILL_HANDLERS[10])(result, context)
    context.record_unreferenced_molecules()
    for error in context.errors:
        error.setdefault("available_mol_ids", sorted(molecules))
        error["code"] = error["reason"]
    return {
        "payload": result,
        "errors": context.errors,
        "roles": {i: m.molecule_type for (_, i), m in index.items()},
    }


__all__ = ["backfill", "backfill_image_molecules", "annotate_image_molecule_types", "BackfillContext"]
