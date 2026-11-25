"""Deterministic, chemistry-only projection; detailed reports remain untouched."""

from __future__ import annotations

import copy
from typing import Any

# Allow fields rather than trying to enumerate every diagnostic field to remove.
CHEMICAL_FIELDS = {
    "reference",
    "common_name",
    "substituents",
    "attachments",
    "assembly_status",
}
CONDITION_FIELDS = {
    "reagents",
    "reagent",
    "catalysts",
    "catalyst",
    "solvents",
    "solvent",
    "temperature",
    "time",
    "duration",
    "pressure",
    "atmosphere",
    "yield",
    "irradiation",
    "ph",
    "pH",
    "workup",
    "text",
    "description",
    "conditions",
    "value",
    "unit",
    "name",
    "amount",
    "equivalents",
    "concentration",
    "reaction_time",
    "reaction_temperature",
    "reaction_conditions",
    "steps",
}


def pick(value: dict[str, Any], fields: set[str]) -> dict[str, Any]:
    return {k: copy.deepcopy(v) for k, v in value.items() if k in fields and v not in (None, "", [], {})}


def condition(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: condition(v) for k, v in value.items() if k in CONDITION_FIELDS} or None
    if isinstance(value, list):
        return [condition(v) for v in value]
    return copy.deepcopy(value)


def molecule(value: dict[str, Any], *, product: bool, chemical_positions: bool = False) -> dict[str, Any]:
    result = pick(value, CHEMICAL_FIELDS)
    if chemical_positions:
        result.update(pick(value, {"position_definitions", "position_status", "position_policy"}))
    assembled = value.get("assembled_molecule") or {}
    structure = value.get("molecular_structure") or {}
    # Failed assemblies must never masquerade as a resolved molecule.
    if assembled.get("valid") is True and assembled.get("canonical_smiles"):
        result["smiles"] = assembled["canonical_smiles"]
    elif not structure.get("is_markush") and structure.get("canonical_smiles"):
        result["smiles"] = structure["canonical_smiles"]
    scaffold = value.get("scaffold_structure") or (structure if structure.get("is_markush") else {})
    markush = value.get("markush")
    if scaffold or isinstance(markush, dict):
        generic = pick(markush or {}, {"description", "variable_options", "expansion_status"})
        # E-SMILES tags link variable names to scaffold atoms; these are chemistry,
        # not PDF coordinates, and cannot be removed from an interpretable template.
        expression = value.get("scaffold_esmiles") or scaffold.get("e_smiles_raw")
        if expression:
            generic["scaffold"] = expression
        if product and isinstance(markush, dict) and markush.get("substitution_cases"):
            generic["substitution_cases"] = [
                pick(case, {"expression", "group_text", "assignments"})
                for case in markush["substitution_cases"]
            ]
        result["markush"] = generic
        series = value.get("compound_series")
        if isinstance(series, dict):
            generic.update(pick(series, {"members", "member_count"}))
    if product:
        variants = value.get("variants") or value.get("extra")
        if variants:
            result["variants"] = [
                molecule(v, product=True, chemical_positions=chemical_positions) for v in variants
            ]
        candidates = value.get("assembly_candidates")
        if candidates:
            # Unnumbered symbolic combinations remain distinct from paper members.
            result["candidates"] = [{**molecule(c, product=True), **pick(c, {"status"})} for c in candidates]
    if not result:
        result["status"] = "unresolved"
    return result


def compact_result(result: dict[str, Any], *, chemical_positions: bool = False) -> dict[str, Any]:
    """Project flowcharts only; other figure delivery contracts are unchanged."""
    extraction = result.get("extraction") or {}
    pathways = extraction.get("pathways")
    image_type = result.get("image_type")
    is_scheme = image_type in (None, "scheme") or str(image_type).startswith("synthetic_scheme")
    if not is_scheme or not isinstance(pathways, list):
        return copy.deepcopy(result)
    delivery: dict[str, Any] = {
        "schema_version": "flowchart-result/1.0",
        "status": result.get("status", "needs_review"),
        "pathways": [],
    }
    for path in pathways:
        steps = []
        previous = [path["base_info"]] if path.get("base_info") else []
        for index, step in enumerate(path.get("steps") or path.get("optimization_steps") or [], 1):
            reactants = step.get("reactants", previous)
            products = step.get("products", [step])
            steps.append(
                {
                    "step": index,
                    **pick(step, {"relationship_type"}),
                    "reactants": [
                        molecule(v, product=False, chemical_positions=chemical_positions) for v in reactants
                    ],
                    "products": [
                        molecule(v, product=True, chemical_positions=chemical_positions) for v in products
                    ],
                    "conditions": condition(step.get("conditions", step.get("condition"))),
                }
            )
            if step.get("variant_links"):
                steps[-1]["variant_links"] = [
                    pick(link, {"source", "target"}) for link in step["variant_links"]
                ]
            previous = products
        delivery["pathways"].append({"steps": steps})
    return delivery


def summarize_result(result: dict[str, Any]) -> dict[str, Any]:
    """Select exact business fields without an LLM, references or chemical inference.

    Keep pathways/steps/reactants/products and actual product variants inline.
    PDF coordinates and provenance are diagnostics; chemical locants are identity.
    The full result and unnumbered candidates remain untouched in intermediate files.
    """
    if result.get("processing_stage") == "backfilled":
        return copy.deepcopy(result)
    compact = compact_result(result, chemical_positions=True)
    if compact.get("schema_version") != "flowchart-result/1.0":
        return compact

    def endpoint(value: dict, *, product: bool) -> dict:
        brief = pick(
            value,
            {
                "reference",
                "common_name",
                "smiles",
                "substituents",
                "position_definitions",
                "position_status",
                "position_policy",
            },
        )
        generic = value.get("markush") or {}
        template = pick(generic, {"scaffold", "variable_options", "member_count"})
        if not template and generic.get("description"):
            template["description"] = generic["description"]
        if template:
            brief["markush"] = template
        if product and value.get("variants"):
            brief["variants"] = [endpoint(v, product=True) for v in value["variants"]]
        if not brief.get("smiles") and not template and not brief.get("variants"):
            brief["smiles"] = None
            status = value.get("assembly_status") or value.get("status")
            brief["status"] = (
                status if status and status not in {"completed", "ok", "success"} else "unresolved"
            )
        return brief

    pathways = []
    for path in compact["pathways"]:
        steps = []
        for step in path["steps"]:
            steps.append(
                {
                    **pick(step, {"step", "relationship_type"}),
                    "reactants": [endpoint(v, product=False) for v in step["reactants"]],
                    "products": [endpoint(v, product=True) for v in step["products"]],
                    "conditions": copy.deepcopy(step["conditions"]),
                    **pick(step, {"variant_links"}),
                }
            )
        pathways.append({"steps": steps})
    return {"schema_version": "flowchart-summary/1.1", "status": compact["status"], "pathways": pathways}
