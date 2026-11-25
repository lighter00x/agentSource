"""Materialize explicit instances after trusted recognition binding, never via a VLM."""

from __future__ import annotations

import re
from typing import Any

from pydantic import ValidationError
from rdkit import Chem

from drugpaper.chem.esmiles import split
from drugpaper.chem.normalize import canonical
from drugpaper.chem.splicer.adapter import infer_role
from drugpaper.chem.splicer.vendor import AtomIndex, Translator, get_abbrev_smi, substitute_markush
from drugpaper.facts.compound_instances import CompoundInstance


def materialize(
    original: dict[str, Any], payload: dict[str, Any], *, blocked_instance_paths: list[str] | None = None
) -> list[dict[str, Any]]:
    """Update only declared instances in their original location; failures have no product."""
    errors: list[dict[str, Any]] = []
    blocked = set(blocked_instance_paths or [])
    primes = str.maketrans({"′": "'", "’": "'", "″": '"'})

    def label_key(value: str) -> str:
        return value.translate(primes).replace("[", "").replace("]", "").strip()

    def instances(raw: Any, bound: Any, path: str) -> None:
        if not isinstance(raw, list) or not isinstance(bound, list):
            errors.append({"code": "invalid_compound_instances", "field_path": path})
            return
        labels: set[tuple[int | None, str]] = set()
        for index, (spec, target) in enumerate(zip(raw, bound, strict=True)):
            location = f"{path}[{index}]"
            if not isinstance(target, dict):
                errors.append({"code": "invalid_compound_instance", "field_path": location})
                continue
            # No stale or model-authored product can survive this stage.
            for key in ("assembled_molecule", "assembly_provenance", "assembly_status"):
                target.pop(key, None)
            target["assembly_status"] = "needs_review"
            try:
                if location in blocked:
                    raise ValueError("reference_assignment_constraints_not_preserved")
                instance = CompoundInstance.model_validate(spec)
                if path == "$.compound_instances" and "rows" in original:
                    if instance.row_index is None or instance.row_index >= len(original["rows"]):
                        raise ValueError("missing_or_invalid_row_index")
                    cells = original["rows"][instance.row_index]
                    if instance.reference not in [c.get("text") for c in cells if isinstance(c, dict)]:
                        raise ValueError("instance_label_not_in_source_row")
                identity = (
                    instance.row_index if path == "$.compound_instances" else None,
                    instance.reference,
                )
                if identity in labels:
                    raise ValueError("duplicate_variant_reference")
                labels.add(identity)
                provenance: dict[str, Any] = {
                    "field_path": location,
                    "reference": instance.reference,
                    "evidence_text": instance.evidence_text,
                    "row_index": instance.row_index,
                    "bindings": target.get("molecular_bindings", []),
                    "substituents": instance.substituents,
                }
                if instance.mol_id is not None:
                    source = target.get("molecular_structure")
                    if not source or source["is_markush"]:
                        raise ValueError("missing_or_incomplete_full_molecule")
                    product = source["canonical_smiles"]
                    provenance["method"] = "direct_recognition"
                else:
                    scaffold = target.get("scaffold_structure")
                    if not scaffold or infer_role(scaffold["e_smiles_raw"]) != "scaffold":
                        raise ValueError("missing_or_unclassified_template")
                    caption = scaffold["e_smiles_raw"].translate(primes)

                    core, annotations = split(caption)
                    mol = Chem.MolFromSmiles(core)
                    if mol is None:
                        raise ValueError("invalid_template")
                    descriptions = Translator.parse_groups(annotations)
                    for desc in descriptions:
                        if isinstance(desc.id, AtomIndex):
                            if int(desc.id) >= mol.GetNumAtoms():
                                raise ValueError("template_annotation_out_of_bounds")
                            if not desc.is_circle and mol.GetAtomWithIdx(int(desc.id)).GetAtomicNum() != 0:
                                raise ValueError("template_annotation_not_on_placeholder")
                    available = {label_key(str(d)) for d in descriptions}
                    from drugpaper.chem.assembly_inputs import project_inputs

                    projection = project_inputs(
                        sorted(available),
                        instance.substituents,
                        instance.position_definitions,
                        instance.attachments,
                    )
                    effective_groups = projection["substituents"]
                    effective_positions = projection["position_definitions"]
                    provenance["assembly_input_projection"] = projection
                    if instance.attachments is not None:
                        provenance["attachments"] = list(instance.attachments)
                    if any(label_key(name) not in available for name in effective_groups):
                        raise ValueError("assignment_variable_absent_from_template")
                    literal_groups = {
                        name.translate(primes): value
                        for name, value in effective_groups.items()
                        if isinstance(value, str)
                    }
                    atom_labels = re.findall(r"<a>[0-9]+:([^<]+)</a>", annotations)
                    explicit_sites = (
                        len(literal_groups) == len(effective_groups)
                        and len(atom_labels) == len(set(atom_labels))
                        and set(atom_labels) == set(literal_groups)
                        and not re.search(r"<[rcd]>", annotations)
                    )
                    if effective_positions and not explicit_sites:
                        raise ValueError("position_definitions_require_explicit_atom_sites")
                    if explicit_sites:
                        from drugpaper.chem.assembly import assemble_explicit

                        product, method = assemble_explicit(caption, literal_groups, effective_positions)
                        provenance["method"] = method
                        provenance["position_definitions"] = effective_positions
                        provenance["scaffold_mol_id"] = instance.scaffold_mol_id
                    else:
                        assignments = {}
                        for name, value in effective_groups.items():
                            if type(value) is int:
                                bindings = [
                                    b
                                    for b in target.get("molecular_bindings", [])
                                    if b["field_path"] == f"{location}.substituents.{name}"
                                ]
                                if len(bindings) != 1:
                                    raise ValueError("unresolved_group")
                                raw_group = bindings[0]["e_smiles_raw"]
                                core, annotations = split(raw_group)
                                if "*" not in core:
                                    raise ValueError("group_has_no_explicit_attachment")
                                # A nested Markush group is not a concrete assignment.
                                if annotations and infer_role(raw_group) in {"scaffold", "sru"}:
                                    raise ValueError("unresolved_group_template")
                                assignments[name.translate(primes)] = core
                            elif value in get_abbrev_smi() or value in {"H", "F", "Cl", "Br", "I"}:
                                assignments[name.translate(primes)] = "[H]" if value == "H" else value
                            else:
                                raise ValueError("unsupported_literal_group_use_mol_id")
                        # Generic E-SMILES attachment semantics; no paper-specific motif dispatch.
                        products = substitute_markush(caption, assignments, max_outputs=2)
                        products = [products] if isinstance(products, str) else products
                        if len(products) != 1:
                            raise ValueError("ambiguous_assembly")
                        product = products[0]
                        provenance["method"] = "esmiles_attachment"
                        provenance["scaffold_mol_id"] = instance.scaffold_mol_id
                if re.fullmatch(r"\([RS]\)-\d+[a-z]?", instance.reference):
                    from drugpaper.facts.reference_delivery import resolve_references

                    stereo = resolve_references(
                        [instance.reference],
                        [{"label": instance.reference, "structure_id": "input"}],
                        [
                            {
                                "structure_id": "input",
                                "node_ref": location,
                                "e_smiles_raw": product,
                                "validation": {"valid": True},
                            }
                        ],
                    )
                    if stereo["errors"]:
                        raise ValueError("unresolved_or_contradictory_stereochemistry")
                    product = stereo["resolved"][instance.reference]["canonical_smiles"]
                    provenance["stereochemistry_from_explicit_label"] = True
                normalized = canonical(split(product)[0])
                if not normalized.valid or "*" in (normalized.canonical_smiles or ""):
                    raise ValueError("invalid_or_incomplete_product")
                target["assembled_molecule"] = normalized.model_dump()
                target["assembly_provenance"] = provenance
                target["assembly_status"] = "completed"
            except (ValidationError, ValueError, RuntimeError, IndexError, TypeError, AttributeError) as exc:
                errors.append(
                    {"code": "compound_instance_failed", "field_path": location, "reason": str(exc)}
                )

    def walk(raw: Any, bound: Any, path: str) -> None:
        if isinstance(raw, dict) and isinstance(bound, dict):
            for key, child in raw.items():
                if key in {"compound_instances", "variants"}:
                    instances(child, bound.get(key), f"{path}.{key}")
                elif key in bound and key not in {"molecule_inventory", "molecular_bindings"}:
                    walk(child, bound[key], f"{path}.{key}")
        elif isinstance(raw, list) and isinstance(bound, list):
            for index, (child, target) in enumerate(zip(raw, bound, strict=True)):
                walk(child, target, f"{path}[{index}]")

    walk(original, payload, "$")
    return errors
