"""Bounded symbolic candidate repair, never full-molecule assembly or member assignment."""

from __future__ import annotations

import copy
import itertools
import json
import re

from jsonschema import Draft202012Validator
from pydantic import BaseModel, ConfigDict, Field

from drugpaper.agents.figure.context import extraction_evidence, pointer
from drugpaper.agents.figure.structure_review import MAX_PATCHES, fingerprint, local_patch_count
from drugpaper.chem.assembly_inputs import local_group
from drugpaper.facts.figure_models import VisualExtraction
from drugpaper.skills.output_integrity import validate_semantic_output


class RepairMarkush(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    product_path: str = Field(
        pattern=r"^/pathways/(0|[1-9][0-9]*)/steps/(0|[1-9][0-9]*)/products/(0|[1-9][0-9]*)$"
    )
    expected_version: int = Field(ge=1)
    evidence_ids: list[str] = Field(min_length=1, max_length=12)
    note: str = Field(min_length=1, max_length=1600)
    expressions: list[str] | None = Field(
        default=None,
        min_length=1,
        max_length=24,
        description="Local expressions read from the current image, e.g. ['XCHYZ'] or ['R', 'X', 'Y']; never copied from recognition-site labels.",
    )


def build_cases(product: dict, expressions: list[str] | None = None) -> list[dict]:
    markush = product.get("markush") or {}
    options = markush.get("variable_options", {})
    if not options:
        raise ValueError("No visible variable options; do not invent them")
    labels = list(
        dict.fromkeys(
            expressions
            if expressions is not None
            else [case["expression"] for case in markush.get("substitution_cases", [])]
        )
    )
    if not labels:
        raise ValueError(
            "Provide expressions read from the current image; no recognition labels are required"
        )
    tokens = re.compile("|".join(re.escape(k) for k in sorted(options, key=len, reverse=True)))
    output = []
    covered = set()
    for label in labels:
        keys = list(dict.fromkeys(tokens.findall(label)))
        if not keys:
            raise ValueError("Attachment label has no matching declared variable; inspect before repairing")
        covered.update(keys)
        combinations = 1
        for key in keys:
            combinations *= len(options[key])
        if combinations > 256:
            raise ValueError(
                "Symbolic candidate repair limit exceeded; keep unsupported constraints explicit"
            )
        cases = {}
        for values in itertools.product(*(options[k] for k in keys)):
            assignment = dict(zip(keys, values, strict=True))
            text = local_group(label, assignment)
            case = cases.setdefault(
                text,
                {
                    "expression": label,
                    "group_text": text,
                    "assignments": [],
                    "basis": "symbolic_substitution",
                    "evidence_text": "Literal substitution of the current figure's declared variable options into its image-derived local expression; candidate only, not a paper member assignment.",
                },
            )
            case["assignments"].append(assignment)
        output.extend(cases.values())
    if covered != options.keys() or len(output) > 128:
        raise ValueError("Variable/attachment coverage is incomplete or exceeds the case limit")
    # Preserve original evidence for cases that already correctly express the same assignments.
    for case in output:
        for old in markush.get("substitution_cases", []):
            if all(old.get(k) == case[k] for k in ("expression", "group_text", "assignments")):
                case["evidence_text"] = old["evidence_text"]
                case["basis"] = old["basis"]
                break
    return output


def repair_markush_cases(state: dict, arguments: dict, schema: dict, inventory: dict) -> dict:
    args = RepairMarkush.model_validate(arguments)
    log = state["exploration"]
    if args.expected_version != log["extraction_version"]:
        raise ValueError("Stale extraction version")
    if local_patch_count(log) >= MAX_PATCHES:
        raise ValueError("Structure patch budget exhausted")
    evidence = {e["id"]: e for e in extraction_evidence(log)}
    if not set(args.evidence_ids) <= evidence.keys() or not any(
        evidence[eid].get("tool") in {"inspect_visual", "inspect_page", "inspect_crop"}
        and evidence[eid].get("result", {}).get("node_ref") == state["node_ref"]
        for eid in args.evidence_ids
    ):
        raise ValueError("Symbolic repair requires current-figure visual evidence")
    previous = state["extraction"]
    candidate = copy.deepcopy(previous)
    product = pointer(candidate, args.product_path)
    if "$missing" in product or not product.get("markush"):
        raise ValueError("Select an existing product Markush endpoint")
    cases = build_cases(product, args.expressions)
    markush = product["markush"]
    before = {k: copy.deepcopy(markush.get(k)) for k in ("substitution_cases", "expansion_status")}
    markush["substitution_cases"] = cases
    markush["expansion_status"] = "complete_for_visible_options"
    parent = candidate
    parts = args.product_path[1:].split("/")
    for part in parts[:-1]:
        parent = parent[int(part)] if isinstance(parent, list) else parent[part]
    parent[int(parts[-1])] = product
    Draft202012Validator(schema).validate(candidate)
    candidate = validate_semantic_output(candidate, VisualExtraction)
    record = {
        "patch_id": f"sp{len(log.get('structure_patches', [])) + 1}",
        "kind": "symbolic_markush_repair",
        "base_version": args.expected_version,
        "product_path": args.product_path,
        "evidence_ids": args.evidence_ids,
        "note": args.note,
        "before": before,
        "after": {"substitution_cases": cases, "expansion_status": "complete_for_visible_options"},
        "before_hash": fingerprint(previous),
        "after_hash": fingerprint(candidate),
    }
    if len(json.dumps(record, ensure_ascii=False).encode()) > 131072:
        raise ValueError("Symbolic repair record exceeds bounded size")
    state["extraction"] = candidate
    log.setdefault("structure_patches", []).append(record)
    log["extraction_version"] += 1
    log["applied_evidence_ids"] = list(
        dict.fromkeys([*log.get("applied_evidence_ids", []), *args.evidence_ids])
    )
    log.pop("structure_audit", None)
    return record
