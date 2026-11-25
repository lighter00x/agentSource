"""Type-independent deterministic binding; detection coverage is not detector recall."""

from __future__ import annotations

import copy
from typing import Any

from drugpaper.chem.esmiles import split
from drugpaper.chem.normalize import canonical


def deliver(
    data: dict[str, Any],
    boxes: list[dict[str, Any]],
    structures: list[dict[str, Any]],
    *,
    blocked_instance_paths: list[str] | None = None,
) -> dict[str, Any]:
    original = copy.deepcopy(data)
    result = copy.deepcopy(data)
    errors: list[dict[str, Any]] = []
    bindings: list[dict[str, Any]] = []
    by_id: dict[int, dict[str, Any]] = {}
    ambiguous: set[int] = set()
    for box in boxes:
        index = box.get("mol_id")
        if type(index) is not int or index < 0 or index in by_id:
            errors.append({"code": "ambiguous_box_id", "mol_id": index})
            if type(index) is int:
                ambiguous.add(index)
            continue
        by_id[index] = box
    candidates: dict[str, list[dict[str, Any]]] = {}
    chemically_valid: dict[int, bool] = {}
    for structure in structures:
        candidates.setdefault(structure["node_ref"], []).append(structure)
        chemically_valid[id(structure)] = canonical(split(structure.get("e_smiles_raw") or "")[0]).valid
    used: set[int] = set()
    resolved: set[int] = set()

    def resolve(index: Any, path: str) -> dict[str, Any] | None:
        if type(index) is not int or index not in by_id or index in ambiguous:
            errors.append({"code": "invalid_mol_id", "mol_id": index, "field_path": path})
            return None
        used.add(index)
        box = by_id[index]
        matches = candidates.get(box["node_ref"], [])
        if len(matches) != 1:
            errors.append({"code": "missing_or_ambiguous_structure", "mol_id": index, "field_path": path})
            return None
        structure = matches[0]
        if (
            not chemically_valid[id(structure)]
            or not structure.get("validation", {}).get("valid")
            or not str(structure.get("e_smiles_raw") or "").strip()
        ):
            errors.append({"code": "invalid_structure", "mol_id": index, "field_path": path})
            return None
        resolved.add(index)
        binding = {
            k: structure.get(k)
            for k in ("structure_id", "node_ref", "e_smiles_raw", "inchikey", "validation")
        }
        binding.update(mol_id=index, field_path=path, segment=box.get("segment", 0), bbox=box.get("bbox"))
        core = split(structure["e_smiles_raw"])[0]
        normalized = canonical(core)
        is_markush = bool(structure.get("is_markush")) or "*" in core
        binding.update(
            canonical_smiles=normalized.canonical_smiles,
            formula=None if is_markush else normalized.formula,
            is_markush=is_markush,
        )
        bindings.append(binding)
        return binding

    def walk(value: Any, path: str) -> None:
        if isinstance(value, list):
            for i, child in enumerate(value):
                walk(child, f"{path}[{i}]")
        elif isinstance(value, dict):
            # Recognition text supplied by a model is never authoritative.
            for key in (
                "esmiles",
                "e_smiles_raw",
                "smiles",
                "canonical_smiles",
                "esmiles_list",
                "molecular_bindings",
                "scaffold_esmiles",
                "backfilled_substituents",
                "molecular_structure",
                "scaffold_structure",
                "reference_binding",
                "assembled_molecule",
                "instance_structure",
                "instance_source_path",
                "assembly_provenance",
                "assembly_status",
                "molecule_objects",
                "assembly_candidates",
                "markush_assembly",
                "candidate_smiles",
            ):
                if value.get(key):
                    errors.append({"code": "model_supplied_structure", "field_path": f"{path}.{key}"})
                value.pop(key, None)
            local = []
            for key in ("mol_id", "scaffold_mol_id"):
                if value.get(key) is not None:
                    binding = resolve(value[key], f"{path}.{key}")
                    if binding:
                        if key == "scaffold_mol_id" and (
                            value.get("compound_series") or value.get("markush")
                        ):
                            # A recognized core is not a complete member of the printed series.
                            binding.update(formula=None, structure_kind="generic_scaffold")
                        local.append(binding)
                        value["esmiles" if key == "mol_id" else "scaffold_esmiles"] = binding["e_smiles_raw"]
                        value["molecular_structure" if key == "mol_id" else "scaffold_structure"] = {
                            name: binding[name]
                            for name in ("e_smiles_raw", "canonical_smiles", "formula", "is_markush")
                        }
            if "mol_ids" in value:
                if not isinstance(value["mol_ids"], list):
                    errors.append({"code": "invalid_mol_ids", "field_path": path})
                else:
                    plural_values = []
                    for i, index in enumerate(value["mol_ids"]):
                        binding = resolve(index, f"{path}.mol_ids[{i}]")
                        plural_values.append(binding["e_smiles_raw"] if binding else None)
                        if binding:
                            local.append(binding)
                    value["esmiles_list"] = plural_values
            substitutions = value.get("substituents")
            if isinstance(substitutions, dict):
                filled_substitutions = {}
                for name, index in substitutions.items():
                    if isinstance(value.get("attachments"), list) and name not in value["attachments"]:
                        continue
                    if isinstance(index, int):
                        binding = resolve(index, f"{path}.substituents.{name}")
                        if binding:
                            local.append(binding)
                            filled_substitutions[name] = binding["e_smiles_raw"]
                    else:
                        filled_substitutions[name] = index
                value["backfilled_substituents"] = filled_substitutions
            children = list(value.items())
            for key, child in children:
                if key not in {
                    "molecule_inventory",
                    "substituents",
                    "attachments",
                    "esmiles",
                    "esmiles_list",
                    "scaffold_esmiles",
                    "backfilled_substituents",
                    "molecular_structure",
                    "scaffold_structure",
                }:
                    walk(child, f"{path}.{key}")
            if local:
                value["molecular_bindings"] = local

    walk(result, "$")
    exclusions: dict[int, dict[str, Any]] = {}
    inventory = data.get("molecule_inventory", [])
    if not isinstance(inventory, list):
        errors.append({"code": "invalid_molecule_inventory"})
        inventory = []
    for item in inventory:
        if not isinstance(item, dict):
            errors.append({"code": "invalid_inventory_item"})
            continue
        index = item.get("mol_id")
        if type(index) is not int or index not in by_id:
            errors.append({"code": "invalid_inventory_id", "mol_id": index})
        elif item.get("disposition") == "excluded":
            if index in exclusions or index in used or not str(item.get("evidence_text") or "").strip():
                errors.append({"code": "conflicting_or_unsupported_exclusion", "mol_id": index})
            else:
                exclusions[index] = item
    coverage = []
    for index, box in sorted(by_id.items()):
        status = (
            "resolved"
            if index in resolved
            else "excluded_pending_review"
            if index in exclusions
            else "unresolved"
        )
        coverage.append(
            {
                **box,
                "status": status,
                "paths": [b["field_path"] for b in bindings if b["mol_id"] == index],
                "exclusion": exclusions.get(index),
            }
        )
        if status == "unresolved":
            errors.append({"code": "unresolved_detected_box", "mol_id": index})
    result["molecule_inventory"] = [
        {
            "mol_id": item["mol_id"],
            "disposition": "referenced"
            if item["status"] == "resolved"
            else "excluded"
            if item["status"] == "excluded_pending_review"
            else "unclear",
            "evidence_text": item["exclusion"]["evidence_text"]
            if item.get("exclusion")
            else "Binding paths: " + ", ".join(item["paths"])
            if item["paths"]
            else "No resolved structural reference",
        }
        for item in coverage
    ]
    from drugpaper.chem.instance_delivery import materialize
    from drugpaper.chem.splicer.adapter import infer_role

    errors.extend(materialize(original, result, blocked_instance_paths=blocked_instance_paths))
    from drugpaper.chem.product_candidates import materialize_products

    errors.extend(materialize_products(result))
    molecule_objects = []
    for index, box in sorted(by_id.items()):
        bound = next((b for b in bindings if b["mol_id"] == index), None)
        molecule_objects.append(
            {
                "object_id": box["node_ref"],
                "mol_id": index,
                "node_ref": box["node_ref"],
                "bbox": box.get("bbox"),
                "segment": box.get("segment", 0),
                "status": "recognized" if bound else "unresolved",
                "role": infer_role(bound["e_smiles_raw"]) if bound else "unknown",
                "structure": bound,
            }
        )
    return {
        "molecule_objects": molecule_objects,
        "original": original,
        "payload": result,
        "errors": errors,
        "roles": {},
        "bindings": bindings,
        "coverage": {
            "status": "needs_review" if errors else "complete_for_detected_boxes",
            "detected_count": len(by_id),
            "resolved_count": len(resolved),
            "excluded_count": len(exclusions),
            "items": coverage,
            "scope": "Detected boxes only; independent image review is required for missed molecules and false positive exclusions.",
        },
    }
