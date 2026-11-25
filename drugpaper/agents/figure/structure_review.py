"""Independent pre-delivery review and transactional, evidence-bound structure edits."""

from __future__ import annotations

import base64
import copy
import hashlib
import json
import re
from typing import Any, Literal

import jsonpatch
from jsonpointer import JsonPointer, JsonPointerException
from jsonschema import Draft202012Validator
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from drugpaper.agents.figure.context import (
    current_structure_inventory,
    extraction_evidence,
    structure_placeholder_inventory,
    structuring_view,
)
from drugpaper.agents.figure.member_normalization import member_normalization_gaps
from drugpaper.facts.figure_models import VisualExtraction
from drugpaper.gateway.base import ImagePart
from drugpaper.persistence.models import ArtifactRow
from drugpaper.skills.output_integrity import reject_empty_replacement, validate_semantic_output

DIMENSIONS = {"topology", "molecule_placeholders", "markush_completeness", "source_scope"}
MAX_REVIEWS = 6
MAX_PATCHES = 5


def local_patch_count(log: dict) -> int:
    total = sum(p.get("kind") != "reference_member_fields" for p in log.get("structure_patches", []))
    return max(0, total - log.get("patch_budget_baseline", 0))


class CoverageItem(BaseModel):
    model_config = ConfigDict(extra="forbid")
    dimension: Literal["topology", "molecule_placeholders", "markush_completeness", "source_scope"]
    verdict: Literal["pass", "fail", "uncertain"]
    evidence: str = Field(min_length=1, max_length=1600)


class StructureIssue(BaseModel):
    model_config = ConfigDict(extra="forbid")
    issue_id: str = Field(min_length=1, max_length=80)
    field_path: str = Field(min_length=1, max_length=240)
    category: Literal[
        "missing_step",
        "incorrect_topology",
        "incorrect_placeholder",
        "missing_markush_case",
        "incorrect_markush_case",
        "unsupported_assignment",
        "other",
    ]
    severity: Literal["error", "warning"]
    description: str = Field(min_length=1, max_length=2000)
    visible_evidence: str = Field(min_length=1, max_length=2000)
    suggested_action: str = Field(min_length=1, max_length=1600)


class StructureAudit(BaseModel):
    model_config = ConfigDict(extra="forbid")
    coverage: list[CoverageItem] = Field(min_length=4, max_length=4)
    issues: list[StructureIssue] = Field(default_factory=list, max_length=32)


class StructureOperation(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    op: Literal["add", "replace", "remove", "test"]
    path: str = Field(min_length=1, max_length=240)
    value: Any = None


class PatchStructure(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    expected_version: int = Field(ge=1)
    operations: list[StructureOperation] = Field(min_length=1, max_length=20)
    evidence_ids: list[str] = Field(min_length=1, max_length=12)
    issue_ids: list[str] = Field(default_factory=list, max_length=32)
    note: str = Field(min_length=1, max_length=2000)


class GetStructure(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    path: str = Field(default="", max_length=240)
    offset: int = Field(default=0, ge=0)
    limit: int = Field(default=10, ge=1, le=20)
    expected_version: int | None = Field(default=None, ge=1)


def enabled(state: dict) -> bool:
    return "scheme_extract" in state.get("chain", [])


def fingerprint(extraction: dict) -> str:
    return hashlib.sha256(json.dumps(extraction, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def audit_passed(state: dict) -> bool:
    audit = state.get("exploration", {}).get("structure_audit", {})
    return audit.get("status") == "passed" and audit.get("extraction_hash") == fingerprint(
        state.get("extraction", {})
    )


def _parts(path: str) -> list[str]:
    if not path.startswith("/"):
        raise ValueError("Use a strict JSON pointer to an existing structure field")
    try:
        parts = JsonPointer(path).parts
    except JsonPointerException as exc:
        raise ValueError("Invalid JSON pointer") from exc
    if any(p.isdigit() and str(int(p)) != p for p in parts):
        raise ValueError("Non-canonical list index")
    return parts


def _lookup(value: Any, parts: list[str]) -> Any:
    try:
        return JsonPointer.from_parts(parts).resolve(value)
    except JsonPointerException as exc:
        raise ValueError("Structure path does not exist") from exc


def get_structure(state: dict, arguments: dict) -> dict:
    """Read one versioned subtree; large containers expose bounded, addressable children."""
    args = GetStructure.model_validate(arguments)
    if not state.get("extraction"):
        raise ValueError("No structure exists yet; extract the figure first")
    if (
        args.expected_version is not None
        and args.expected_version != state["exploration"]["extraction_version"]
    ):
        raise ValueError(
            "Structure version changed; reread without expected_version to inspect the current version"
        )
    parts = _parts(args.path) if args.path else []
    value = _lookup(structuring_view(state["extraction"]), parts)
    result = {
        "ok": True,
        "extraction_version": state["exploration"]["extraction_version"],
        "path": args.path,
        "value_hash": fingerprint(value),
    }
    if not isinstance(value, (dict, list)):
        if len(json.dumps(value, ensure_ascii=False).encode()) > 12000:
            result.update(value_omitted=True, reason="Scalar exceeds read budget")
        else:
            result["value"] = copy.deepcopy(value)
        return result
    keys = list(value) if isinstance(value, dict) else list(range(len(value)))
    children = []
    for key in keys[args.offset : args.offset + args.limit]:
        child = value[key]
        item = {"path": JsonPointer.from_parts([*parts, str(key)]).path}
        if len(json.dumps(child, ensure_ascii=False).encode()) <= 800:
            item["value"] = copy.deepcopy(child)
        else:
            item.update(value_omitted=True, value_hash=fingerprint(child))
            if isinstance(child, (dict, list)):
                item["size"] = len(child)
        children.append(item)
    result.update(children=children, total=len(keys), has_more=args.offset + args.limit < len(keys))
    # Small subtrees remain convenient to read; large ones are never silently truncated.
    if (
        len(json.dumps(value, ensure_ascii=False).encode()) <= 12000
        and not result["has_more"]
        and not args.offset
    ):
        result["value"] = copy.deepcopy(value)
    else:
        result["value_omitted"] = True
    return result


def preview_structure_patch(state: dict, arguments: dict, schema: dict, inventory: dict) -> dict:
    """Run the same guards and schema checks on an isolated copy, without review or commit."""
    pending = copy.deepcopy(state)
    record = patch_structure(pending, arguments, schema, inventory)
    return {
        "ok": True,
        "dry_run": True,
        "extraction_version": state["exploration"]["extraction_version"],
        "patch": record,
        "note": "Preview only; submit patch_structure with the same version to commit and review",
    }


def composite_markush_gaps(extraction: dict, inventory: dict) -> list[dict]:
    """A composite attachment needs joint assignments, not separate variable lists.

    This does not infer chemistry, symmetry, constraints or Cartesian member counts;
    the independent visual reviewer still verifies the actual visible possibilities.
    """
    issues = []
    for pi, pathway in enumerate(extraction.get("pathways", [])):
        for si, step in enumerate(pathway.get("steps", [])):
            for mi, product in enumerate(step.get("products", [])):
                markush = product.get("markush") or {}
                options = markush.get("variable_options", {})
                if not options:
                    continue
                pattern = re.compile("|".join(re.escape(k) for k in sorted(options, key=len, reverse=True)))
                labels = list(dict.fromkeys(c["expression"] for c in markush.get("substitution_cases", [])))
                for label in labels:
                    variables = set(pattern.findall(label))
                    if len(variables) < 2:
                        continue
                    joint = [
                        (case, a)
                        for case in markush.get("substitution_cases", [])
                        for a in case.get("assignments", [])
                        if variables <= a.keys()
                    ]
                    if joint:
                        from drugpaper.chem.formula import composition

                        for ci, (case, assignment) in enumerate(joint):
                            expanded = pattern.sub(lambda m, values=assignment: values.get(m[0], m[0]), label)
                            text = case.get("group_text", "").lstrip("-–− ")
                            if text == label and any(assignment[k] != k for k in variables):
                                issues.append(
                                    {
                                        "issue_id": f"unsubstituted-group-{pi}-{si}-{mi}-{ci}",
                                        "field_path": f"/pathways/{pi}/steps/{si}/products/{mi}/markush/substitution_cases",
                                        "category": "incorrect_markush_case",
                                        "severity": "error",
                                        "description": "Local group text still contains the original variable placeholder rather than the substituted group.",
                                        "visible_evidence": f"group_text={text!r} was not substituted for assignment {assignment}.",
                                        "suggested_action": "Use repair_markush_cases to repair this product's visible symbolic cases, keeping member mappings separate.",
                                    }
                                )
                                continue
                            try:
                                expected, actual = composition(expanded), composition(text)
                            except ValueError:
                                # Abbreviations/complex notation require visual review, not a guessed parser.
                                continue
                            if expected != actual:
                                issues.append(
                                    {
                                        "issue_id": f"local-group-atoms-{pi}-{si}-{mi}-{ci}",
                                        "field_path": f"/pathways/{pi}/steps/{si}/products/{mi}/markush/substitution_cases",
                                        "category": "incorrect_markush_case",
                                        "severity": "error",
                                        "description": "Local group text changes explicit atom counts compared with the attachment expression and joint assignment.",
                                        "visible_evidence": f"Substitute {assignment} into the current attachment {label!r}; compare with local group text {text!r}.",
                                        "suggested_action": "Correct only group_text/assignments; preserve literal atoms, including explicit H, and do not append extra hydrogens. This checks a local group, not a complete molecule.",
                                    }
                                )
                        continue
                    issues.append(
                        {
                            "issue_id": f"joint-attachment-{pi}-{si}-{mi}-{len(issues)}",
                            "field_path": f"/pathways/{pi}/steps/{si}/products/{mi}/markush",
                            "category": "missing_markush_case",
                            "severity": "error",
                            "description": "Composite attachment lacks joint substituent assignments; independent variable choices are not a full group expansion.",
                            "visible_evidence": f"Current scaffold attachment label {label!r} contains the declared variables {sorted(variables)}.",
                            "suggested_action": "Add complete local-group cases using joint assignments to these variables, covering the visible allowed possibilities and constraints; retain explicit hydrogens and do not assign paper members by Cartesian count.",
                        }
                    )
    return issues


def candidate_definition_gaps(extraction: dict, inventory: dict) -> list[dict]:
    """Validate candidate definitions with the same deterministic enumerator as delivery,
    without assembling or persisting any molecular structure."""
    from drugpaper.chem.product_candidates import combinations

    issues = []
    for pi, pathway in enumerate(extraction.get("pathways", [])):
        for si, step in enumerate(pathway.get("steps", [])):
            for mi, product in enumerate(step.get("products", [])):
                markush = product.get("markush")
                if not markush:
                    continue
                reason = None
                try:
                    combinations(markush)
                except ValueError as exc:
                    reason = str(exc)
                if reason:
                    issues.append(
                        {
                            "issue_id": f"candidate-definitions-{pi}-{si}-{mi}",
                            "field_path": f"/pathways/{pi}/steps/{si}/products/{mi}/markush/substitution_cases",
                            "category": "missing_markush_case",
                            "severity": "error",
                            "description": "Image-derived candidate definitions are incomplete or ambiguous.",
                            "visible_evidence": reason,
                            "suggested_action": "Locally correct/append cases for the image-derived expressions and visible options, including joint assignments for composite groups. Do not change variables to match recognition labels; compatibility is checked during backfill/assembly. Do not infer paper member mappings.",
                        }
                    )
    return issues


async def review_structure(agent: Any, state: dict) -> dict:
    log = state["exploration"]
    normalize_position_fields(state)
    previous = log.get("structure_audit", {})
    if (
        previous.get("extraction_hash") == fingerprint(state["extraction"])
        and previous.get("status")
        in {
            "passed",
            "needs_revision",
            "uncertain",
        }
        and not (previous.get("status") == "passed" and member_normalization_gaps(state["extraction"]))
    ):
        event = {
            "kind": "structure_review_cached",
            **copy.deepcopy(previous),
            "reused": True,
            "turn": log.get("active_turn", 0),
            "parent_call_id": log.get("active_call_id"),
        }
        log.setdefault("steps", []).append(copy.deepcopy(event))
        await agent._exploration_event(state, event)
        return copy.deepcopy(previous)
    if len(log.get("structure_reviews", [])) >= MAX_REVIEWS:
        raise ValueError("Independent structure review budget exhausted; delivery remains blocked")
    _, ctx = await agent._ctx(state)
    assert ctx.materials is not None
    images = list(ctx.materials.images)
    # A fresh reviewer sees the original and the existing numbered image, no agent history
    # or reference-page images. Attachment names are supplied without recognized SMILES.
    async with agent.services.db.session(state["user_id"]) as session:
        som = (
            await session.execute(
                select(ArtifactRow)
                .where(
                    ArtifactRow.user_id == state["user_id"],
                    ArtifactRow.target_ref == state["node_ref"],
                    ArtifactRow.artifact_type == "som_image",
                    ArtifactRow.is_active.is_(True),
                )
                .order_by(ArtifactRow.version.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
    if som:
        for segment in (som.payload or {}).get("images", []):
            images.append(
                ImagePart(
                    data_b64=base64.b64encode(
                        await agent.services.store_for(state["user_id"]).get(segment["blob_ref"])
                    ).decode(),
                    mime="image/png",
                )
            )
    bundle, _ = await agent._ctx(state)
    skill = next(s for s in await bundle.registry.list_visible() if s.name == "scheme_extract")
    from drugpaper.agents.figure.reference_assignments import reference_constraint_errors

    source_errors = reference_constraint_errors(state["extraction"], log)
    inventory = structure_placeholder_inventory(ctx.materials)
    reference_summary = {
        "scope": "registered_reference_assignments_not_current_figure_claims",
        "records": [
            {
                "supplement_id": record["supplement_id"],
                "product_path": record["product_path"],
                "references": [a["reference"] for a in record["assignments"]],
                "sources": record["sources"],
            }
            for record in log.get("reference_assignments", [])
        ],
        "constraint_errors": source_errors[:32],
        "constraint_error_count": len(source_errors),
        "note": "Registration records actual detailed source reading, ownership and applied member assignments. It is provenance, not a new independent chemical truth certificate. Current-image review does not re-audit external images.",
    }
    result = await agent._run_skill(
        state,
        "structure_audit",
        {
            "extraction": structuring_view(state["extraction"]),
            "extraction_rules": skill.system_text() + "\n" + (skill.path / "prompts/task.md").read_text(),
            "structure_inventory": structure_placeholder_inventory(ctx.materials),
            "scope": "current_target_figure_only",
            "reference_source_summary": reference_summary,
            "host_field_checks": {
                "authority": "Schema and field presence are host checks; visual review checks meaning against the image, not whether an already-listed field exists.",
                "attachment_selections": [
                    {k: v for k, v in row.items() if k != "expected_sites"}
                    for row in attachment_selection_summary(state["extraction"], {})
                ],
                "topology": topology_summary(state["extraction"]),
            },
        },
        images=images,
        category="exploration",
        force=True,
    )
    parsed = StructureAudit.model_validate(result)
    if {x.dimension for x in parsed.coverage} != DIMENSIONS:
        raise ValueError("Reviewer must explicitly inspect every required dimension")
    if len({x.issue_id for x in parsed.issues}) != len(parsed.issues):
        raise ValueError("Review issue IDs must be unique")
    for issue in parsed.issues:
        _lookup(state["extraction"], _parts(issue.field_path))
    inventory = structure_placeholder_inventory(ctx.materials)
    additional = (
        composite_markush_gaps(state["extraction"], inventory)
        + candidate_definition_gaps(state["extraction"], inventory)
        + series_relation_gaps(state["extraction"])
        + member_normalization_gaps(state["extraction"])
    )
    if additional:
        parsed.issues.extend(StructureIssue.model_validate(i) for i in additional)
        for item in parsed.coverage:
            relevant = [
                i
                for i in additional
                if item.dimension
                == ("topology" if i["category"] == "incorrect_topology" else "markush_completeness")
            ]
            if relevant:
                item.verdict = "fail"
                item.evidence += " Host checks: " + "; ".join(i["description"] for i in relevant)
    full_inventory = current_structure_inventory(ctx.materials)
    has_any_labels = any(s.get("attachment_labels") for s in full_inventory.get("structures", []))
    selector_inventory = full_inventory if has_any_labels else inventory
    selectors = attachment_selection_gaps(state["extraction"], selector_inventory) + position_policy_gaps(
        state["extraction"]
    )
    if selectors:
        parsed.issues.extend(StructureIssue.model_validate(i) for i in selectors)
        if any(item.get("severity") == "error" for item in selectors):
            for item in parsed.coverage:
                if item.dimension == "molecule_placeholders":
                    item.verdict = "fail"
                    item.evidence += " Host check: explicit attachment/position selection needs correction."
    failed = any(x.verdict == "fail" for x in parsed.coverage) or any(
        x.severity == "error" for x in parsed.issues
    )
    uncertain = any(x.verdict == "uncertain" for x in parsed.coverage)
    audit = {
        **parsed.model_dump(),
        "review_id": f"sr{len(log.get('structure_reviews', [])) + 1}",
        "status": "needs_revision" if failed else "uncertain" if uncertain else "passed",
        "extraction_hash": fingerprint(state["extraction"]),
        "extraction_version": log["extraction_version"],
        "scope": "current_target_figure_only",
    }
    log.setdefault("structure_reviews", []).append(copy.deepcopy(audit))
    log["structure_audit"] = audit
    from drugpaper.agents.figure.process_trace import image_references

    event = {
        "kind": "structure_review",
        **audit,
        "turn": log.get("active_turn", 0),
        "parent_call_id": log.get("active_call_id"),
        "image_refs": await image_references(agent.services.store_for(state["user_id"]), images),
    }
    log.setdefault("steps", []).append(copy.deepcopy(event))
    await agent._exploration_event(state, event)
    return audit


def attachment_selection_gaps(extraction: dict, inventory: dict) -> list[dict]:
    issues = []
    labels_by_mol = {
        s["mol_id"]: s.get("attachment_labels")
        for s in inventory.get("structures", [])
        if isinstance(s.get("mol_id"), int)
    }

    def visit(value, path):
        if isinstance(value, dict):
            groups = value.get("substituents")
            mol_id = value.get("scaffold_mol_id")
            if isinstance(mol_id, int) and isinstance(groups, dict) and groups:
                selected = value.get("attachments")
                labels = labels_by_mol.get(mol_id)
                valid = (
                    isinstance(selected, list)
                    and bool(selected)
                    and all(isinstance(k, str) and k in groups for k in selected)
                    and len(set(selected)) == len(selected)
                )
                if valid and labels is not None:
                    valid = bool(labels) and set(selected) == set(labels)
                if not valid:
                    evidence = f"attachments={selected!r}; declared substituent keys={list(groups)!r}."
                    if labels is not None:
                        evidence += f" Current-figure attachment sites={list(labels)!r}."
                    issues.append(
                        {
                            "issue_id": f"attachment-selection-{len(issues) + 1}",
                            "field_path": path,
                            "category": "incorrect_placeholder",
                            "severity": "error",
                            "description": (
                                "Each assembled member must select exactly the current scaffold's "
                                "attachment sites; explanatory variable fields are not additional sites."
                                if labels is None
                                else "attachments must match the current scaffold attachment labels exactly; "
                                "a scaffold without labels cannot be assembled."
                            ),
                            "visible_evidence": evidence,
                            "suggested_action": (
                                "Call inspect_attachment_sites, then correct attachments to exactly the "
                                "listed attachment sites. If the scaffold has no sites, choose a Markush "
                                "scaffold or record unsupported_representation; do not invent labels."
                            ),
                        }
                    )
            for key, child in value.items():
                visit(child, path + "/" + key)
        elif isinstance(value, list):
            for i, child in enumerate(value):
                visit(child, path + "/" + str(i))

    visit(extraction, "")
    return issues


LOCANT_VALUE = re.compile(r"^\s*\d+\s*[′'’]?\s*(?:[-–—]\s*[A-Za-z]|\s*$)")


def _locant_like(value: Any) -> bool:
    return isinstance(value, str) and bool(LOCANT_VALUE.match(value))


def normalize_position_fields(state: dict) -> list[dict]:
    """Host-side defaults for position_policy/position_status.

    The VLM owns the evidence, but a missing status or a not_applicable policy
    contradicted by supplied locants must not turn into repeated review errors.
    This is idempotent and only fills derivable values; it never invents locants.
    """
    changes: list[dict] = []
    log = state.setdefault("exploration", {})

    def note(path: str, change: str) -> None:
        changes.append({"path": path, "change": change})

    def visit(value: Any, path: str = "") -> None:
        if isinstance(value, dict):
            variants = value.get("variants")
            if isinstance(variants, list) and variants:
                policy = value.get("position_policy")
                if not isinstance(policy, dict) and any(
                    isinstance(v, dict) and v.get("position_definitions") for v in variants
                ):
                    policy = {
                        "requirement": "required",
                        "scope": "per_member",
                        "source": "derived_from_supplied_locants",
                        "basis": "Host: supplied position_definitions require an explicit policy.",
                        "evidence_text": "",
                    }
                    value["position_policy"] = policy
                    note(path, "policy_added_required")
                if isinstance(policy, dict):
                    requirement = policy.get("requirement")
                    has_supplied = any(
                        isinstance(v, dict) and v.get("position_definitions") for v in variants
                    ) or _markush_has_locants(value)
                    if requirement == "not_applicable" and has_supplied:
                        # Supplied locants prove the series is position-bearing, but
                        # not every printed member necessarily has one. Optional keeps
                        # missing members reviewable without blocking the flowchart.
                        policy["requirement"] = "optional"
                        policy["basis"] = (
                            str(policy.get("basis", "")).strip()
                            + " Host: supplied locants contradict not_applicable; partial members allowed."
                        ).strip()
                        requirement = "optional"
                        note(path, "policy_upgraded_to_optional")
                    for index, variant in enumerate(variants):
                        if not isinstance(variant, dict):
                            continue
                        definitions = variant.get("position_definitions") or {}
                        status = variant.get("position_status")
                        if definitions and status is None:
                            variant["position_status"] = "provided"
                            note(f"{path}/variants/{index}", "status_provided")
                        elif not definitions and status is None:
                            if requirement == "not_applicable":
                                variant["position_status"] = "not_applicable"
                                note(f"{path}/variants/{index}", "status_not_applicable")
                            elif requirement in {"required", "optional"}:
                                variant["position_status"] = "missing"
                                note(f"{path}/variants/{index}", "status_missing")
            for key, child in value.items():
                if key in {"position_policy", "markush", "compound_series"}:
                    continue
                visit(child, path + "/" + key)
        elif isinstance(value, list):
            for index, child in enumerate(value):
                visit(child, path + f"/{index}")

    visit(state.get("extraction") or {})
    if changes:
        log.setdefault("position_normalizations", []).extend(changes)
    return changes


def _markush_has_locants(value: dict) -> bool:
    markush = value.get("markush")
    if not isinstance(markush, dict):
        return False
    options = markush.get("variable_options") or {}
    return any(
        _locant_like(option) for values in options.values() if isinstance(values, list) for option in values
    )


def position_policy_gaps(extraction: dict) -> list[dict]:
    """Host check: only declared-required position evidence blocks review.

    A missing position_definitions is not an error when the endpoint declares
    not_applicable/exhausted, or when one member is explicitly not_printed.
    """
    issues: list[dict] = []

    def add(path: str, severity: str, description: str, evidence: str, action: str) -> None:
        issues.append(
            {
                "issue_id": f"position-policy-{len(issues) + 1}",
                "field_path": path,
                "category": "incorrect_placeholder",
                "severity": severity,
                "description": description,
                "visible_evidence": evidence,
                "suggested_action": action,
            }
        )

    def check(value: dict, path: str, policy: dict | None, inherited_position: bool) -> None:
        definitions = value.get("position_definitions") or {}
        status = value.get("position_status")
        requirement = (policy or {}).get("requirement")
        substituent_locants = any(_locant_like(item) for item in (value.get("substituents") or {}).values())
        has_position = bool(definitions) or substituent_locants or inherited_position
        evidence = (
            f"position_status={status!r}; position_definitions={definitions!r}; "
            f"substituents={value.get('substituents')!r}"
        )
        if requirement is None:
            return  # Legacy records: keep the audit model's judgement.
        if requirement == "exhausted":
            return
        if requirement == "not_applicable":
            if has_position:
                add(
                    path,
                    "error",
                    "Endpoint declares not_applicable positions but position evidence is present.",
                    evidence,
                    "Set requirement=required/optional and reconcile the visible locants, or remove the unsupported position claim.",
                )
            return
        if requirement == "required":
            if status in {"provided", "exhausted"} and definitions:
                return
            if status == "not_printed":
                return
            if status == "not_applicable" and not has_position:
                return
            if status == "unreadable":
                add(
                    path,
                    "error",
                    "A required position is printed but unreadable in the current image.",
                    evidence,
                    "Inspect the higher-resolution page/table. If still unreadable after source investigation, set position_status=exhausted with the checked sources.",
                )
            elif status == "provided":
                add(
                    path,
                    "error",
                    "position_status=provided has no position_definitions.",
                    evidence,
                    "Fill position_definitions or change the status; do not mark provided without a locant.",
                )
            elif status == "not_applicable":
                add(
                    path,
                    "error",
                    "A required-position series cannot mark this member not_applicable while position evidence exists.",
                    evidence,
                    "Supply the visible locant or investigate the source column; only use not_applicable when no position evidence exists.",
                )
            else:
                add(
                    path,
                    "error",
                    "Required per-member position is missing.",
                    evidence,
                    "Read the printed position column/source, add position_definitions, or record why it is not_printed/unreadable. Do not retry without new evidence.",
                )
            return
        if requirement == "optional":
            if status in {"provided", "not_printed", "exhausted"}:
                return
            if status == "not_applicable" and not has_position:
                return
            severity = "error" if status == "unreadable" else "warning"
            add(
                path,
                severity,
                "Optional position is missing or unreadable.",
                evidence,
                "Supply it if visible, else record not_printed; this does not block the flowchart review.",
            )
            return
        if requirement == "unknown":
            if status == "exhausted":
                return
            if status == "not_applicable" and has_position:
                add(
                    path,
                    "error",
                    "Declares not_applicable while position evidence exists.",
                    evidence,
                    "Investigate the printed locant and set requirement/status from evidence.",
                )
                return
            add(
                path,
                "warning",
                "Position requirement is unresolved; complete source investigation before retrying.",
                evidence,
                "Check the caption, position column and related tables; then set required/optional/not_applicable/exhausted from evidence.",
            )

    def visit(value: Any, path: str = "", policy: dict | None = None, inherited: bool = False) -> None:
        if isinstance(value, dict):
            own_policy = value.get("position_policy") or policy
            has_position = inherited or _markush_has_locants(value)
            is_endpoint = any(
                key in value for key in ("variants", "compound_series", "markush", "position_policy")
            )
            if (
                not is_endpoint
                and isinstance(value.get("scaffold_mol_id"), int)
                and isinstance(value.get("substituents"), dict)
            ):
                check(value, path, own_policy, has_position)
            for key, child in value.items():
                if key in {"position_policy", "markush", "compound_series"}:
                    continue
                visit(child, path + "/" + key, own_policy, has_position)
        elif isinstance(value, list):
            for index, child in enumerate(value):
                visit(child, path + f"/{index}", policy, inherited)

    visit(extraction)
    return issues


def series_relation_gaps(extraction: dict) -> list[dict]:
    """A condition-free focus on an existing member is not a new optimization."""
    issues = []
    for pi, pathway in enumerate(extraction.get("pathways", [])):
        for si, step in enumerate(pathway.get("steps", [])):
            sources, targets = step.get("reactants", []), step.get("products", [])
            if (
                step.get("relationship_type") != "optimization"
                or step.get("conditions")
                or len(sources) != 1
                or len(targets) != 1
            ):
                continue
            members = (
                sources[0].get("compound_series", {}).get("members", [])
                if sources[0].get("compound_series")
                else []
            )
            if not members or targets[0].get("reference") not in members:
                continue
            issues.append(
                {
                    "issue_id": f"series-member-relation-{pi}-{si}",
                    "field_path": f"/pathways/{pi}/steps/{si}/relationship_type",
                    "category": "incorrect_topology",
                    "severity": "warning",
                    "description": "This arrow focuses on a member already in its source series; the overall optimization theme does not establish a new structure optimization for this step. This is a semantic warning and does not block the structural backfill path.",
                    "visible_evidence": "The saved source member set contains the target reference and no reaction conditions are recorded.",
                    "suggested_action": "Compare this exact arrow and its endpoints. Use selection for focusing an existing member, without inventing an activity ranking; preserve unresolved if evidence conflicts. Do not alter another step.",
                }
            )
    return issues


def topology_summary(extraction: dict) -> list[dict]:
    """Flatten actual step endpoints so nested variants cannot masquerade as edges."""
    result = []
    for pi, pathway in enumerate(extraction.get("pathways", [])):
        for si, step in enumerate(pathway.get("steps", [])):
            item = {"path": f"/pathways/{pi}/steps/{si}", "relationship_type": step.get("relationship_type")}
            for side in ("reactants", "products"):
                item[side] = [
                    {k: endpoint.get(k) for k in ("mol_id", "scaffold_mol_id", "reference")}
                    for endpoint in step.get(side, [])
                ]
            result.append(item)
    return result


def attachment_selection_summary(extraction: dict, inventory: dict) -> list[dict]:
    """Small, exact selector view so the reviewer need not rediscover JSON fields."""
    sites = {s["mol_id"]: s.get("attachment_labels", []) for s in inventory.get("structures", [])}
    records = []

    def visit(value, path):
        if isinstance(value, dict):
            if value.get("scaffold_mol_id") is not None and value.get("substituents"):
                records.append(
                    {
                        "path": path,
                        "reference": value.get("reference"),
                        "attachments_present": "attachments" in value,
                        "attachments": value.get("attachments"),
                        "substituent_keys": list(value["substituents"]),
                        "expected_sites": sites.get(value["scaffold_mol_id"], []),
                    }
                )
            for key, item in value.items():
                visit(item, path + "/" + key.replace("~", "~0").replace("/", "~1"))
        elif isinstance(value, list):
            for i, item in enumerate(value):
                visit(item, path + "/" + str(i))

    visit(extraction, "")
    return records


def patch_structure(state: dict, arguments: dict, schema: dict, inventory: dict) -> dict:
    args = PatchStructure.model_validate(arguments)
    log = state["exploration"]
    if args.expected_version != log["extraction_version"]:
        raise ValueError("Stale structure version; obtain the current extraction before patching")
    if local_patch_count(log) >= MAX_PATCHES:
        raise ValueError("Structure patch budget exhausted")
    if len(json.dumps(arguments, ensure_ascii=False).encode()) > 32768:
        raise ValueError("Patch too large; modify only the affected fields")
    evidence = {e["id"]: e for e in extraction_evidence(log)}
    if not set(args.evidence_ids) <= evidence.keys():
        raise ValueError(
            "Patch requires actual retained text/detail evidence IDs, never preview or invented IDs"
        )
    issue_ids = {i["issue_id"] for i in log.get("structure_audit", {}).get("issues", [])}
    if not set(args.issue_ids) <= issue_ids:
        raise ValueError("Unknown review issue ID")
    previous = state["extraction"]
    candidate = copy.deepcopy(previous)
    changes = []
    protected = {"mol_id", "scaffold_mol_id"}
    allowed_ids = {s["mol_id"] for s in inventory.get("structures", [])}

    def check_values(value: Any, field: str = "") -> None:
        if field.lower() in {
            "smiles",
            "esmiles",
            "e_smiles_raw",
            "smiles_core",
            "canonical_smiles",
            "formula",
            "inchi",
            "inchikey",
            "assembled_molecule",
            "assembly_provenance",
        }:
            raise ValueError("Structure patches must remain ID-only; chemical strings are forbidden")
        if isinstance(value, dict):
            for key, child in value.items():
                if field == "substituents" and type(child) is int and child not in allowed_ids:
                    raise ValueError("Substituent placeholder is outside the current figure inventory")
                if key.lower() in {
                    "smiles",
                    "esmiles",
                    "e_smiles_raw",
                    "canonical_smiles",
                    "formula",
                    "assembled_molecule",
                    "assembly_provenance",
                }:
                    raise ValueError("Structure patches must remain ID-only; chemical strings are forbidden")
                check_values(child, key)
        elif isinstance(value, list):
            for child in value:
                check_values(child, field)
        elif (
            field in protected and value is not None and (type(value) is not int or value not in allowed_ids)
        ):
            raise ValueError("Molecule placeholder is outside the current figure inventory")

    for op in args.operations:
        if op.op != "remove" and "value" not in op.model_fields_set:
            raise ValueError(f"{op.op} requires an explicit value, including null when intended")
        parts = _parts(op.path)
        if parts[0] not in {
            "pathways",
            "optimization_steps",
            "compound_instances",
            "information_gaps",
            "molecule_inventory",
            "status",
            "evidence_text",
        }:
            raise ValueError("Patch targets a field outside the structure template")
        if len(parts) == 1 and parts[0] not in {"status", "evidence_text"}:
            raise ValueError(
                "Whole collection replacement is forbidden; patch the affected field or append an item"
            )
        parent = _lookup(candidate, parts[:-1])
        key = parts[-1]
        if op.op == "test":
            try:
                jsonpatch.JsonPatch([op.model_dump()]).apply(candidate)
            except (jsonpatch.JsonPatchException, JsonPointerException) as exc:
                raise ValueError(
                    f"Patch precondition failed at {op.path}; reread the current fragment"
                ) from exc
            continue
        check_values(op.value, key)
        if "markush" in parts and not any(
            evidence[eid].get("tool") in {"inspect_visual", "inspect_page", "inspect_crop"}
            and evidence[eid].get("result", {}).get("node_ref") == state["node_ref"]
            for eid in args.evidence_ids
        ):
            raise ValueError("Current figure Markush options require current-figure visual evidence")
        if isinstance(parent, list):
            if op.op == "add" and key == "-":
                changes.append({"path": op.path, "before": None, "after": op.value})
            elif key.isdigit() and int(key) < len(parent) and op.op in {"remove", "replace"}:
                before = copy.deepcopy(parent[int(key)])
                if op.op == "remove":
                    if not args.issue_ids:
                        raise ValueError("Removing a collection item requires a current review issue")
                else:
                    if isinstance(before, (dict, list)):
                        raise ValueError("Modify the affected fields inside an existing object")
                changes.append(
                    {"path": op.path, "before": before, "after": None if op.op == "remove" else op.value}
                )
            else:
                raise ValueError(
                    f"Invalid {op.op} at {op.path}: appending requires op='add' with a /- path. Use replace only on an existing scalar field; the entire patch was rolled back."
                )
        elif isinstance(parent, dict):
            if op.op == "add" and key in parent:
                raise ValueError("add requires a missing field; use replace for existing scalar fields")
            if op.op in {"replace", "remove"} and key not in parent:
                raise ValueError("Patch field does not exist")
            before = copy.deepcopy(parent.get(key))
            scalar_list = (
                op.op == "replace"
                and isinstance(before, list)
                and isinstance(op.value, list)
                and all(not isinstance(item, (dict, list)) for item in [*before, *op.value])
            )
            if isinstance(before, (list, dict)) and not scalar_list:
                raise ValueError("Cannot replace/remove an entire object or collection; patch its children")
            changes.append(
                {
                    "path": op.path,
                    "before": before,
                    "after": None if op.op == "remove" else copy.deepcopy(op.value),
                }
            )
        else:
            raise ValueError("Patch parent is not an object or collection")
        try:
            candidate = jsonpatch.JsonPatch([op.model_dump()]).apply(candidate)
        except (jsonpatch.JsonPatchException, JsonPointerException) as exc:
            raise ValueError(f"Invalid patch at {op.path}; the entire patch was rolled back") from exc
    if not changes:
        raise ValueError("Patch contains no modifications; use get_structure for reads")
    reject_empty_replacement(previous, candidate)
    Draft202012Validator(schema).validate(candidate)
    candidate = validate_semantic_output(candidate, VisualExtraction)
    from drugpaper.agents.figure.reference_assignments import supplement_errors

    source_errors = supplement_errors(candidate, log.get("reference_assignments", []))
    previous_errors = {
        json.dumps(e, sort_keys=True)
        for e in supplement_errors(previous, log.get("reference_assignments", []))
    }
    new_errors = [e for e in source_errors if json.dumps(e, sort_keys=True) not in previous_errors]
    if new_errors:
        raise ValueError(
            "Patch conflicts with recorded member evidence: "
            + json.dumps(new_errors, ensure_ascii=False)
            + ". If the source record is wrong, inspect the actual image, then use record_reference_assignments with the indicated supersedes IDs and correction_reason; keep unrelated members."
        )
    # The raw candidate never contains calculated molecules. Source IDs remain separately auditable.
    record = {
        "patch_id": f"sp{len(log.get('structure_patches', [])) + 1}",
        "base_version": args.expected_version,
        "evidence_ids": args.evidence_ids,
        "issue_ids": args.issue_ids,
        "note": args.note,
        "operations": [o.model_dump() for o in args.operations],
        "changes": changes,
        "before_hash": fingerprint(previous),
        "after_hash": fingerprint(candidate),
    }
    state["extraction"] = candidate
    log.setdefault("structure_patches", []).append(record)
    log["extraction_version"] += 1
    log["applied_evidence_ids"] = list(
        dict.fromkeys([*log.get("applied_evidence_ids", []), *args.evidence_ids])
    )
    log.pop("structure_audit", None)
    return record


async def apply_reference_patch(agent: Any, state: dict) -> dict:
    """Apply confirmed member fields locally, without asking a model to regenerate the figure."""
    from drugpaper.agents.figure.reference_assignments import apply_reference_assignments

    log = state["exploration"]
    if sum(p.get("kind") == "reference_member_fields" for p in log.get("structure_patches", [])) >= 16:
        raise ValueError("Reference member patch budget exhausted")
    previous = copy.deepcopy(state["extraction"])
    pending = copy.deepcopy(log)
    pending["applied_evidence_ids"] = list(
        dict.fromkeys(
            [
                *log.get("applied_evidence_ids", []),
                *(s["evidence_id"] for r in log.get("reference_assignments", []) for s in r["sources"]),
            ]
        )
    )
    bundle, ctx = await agent._ctx(state)
    inventory = structure_placeholder_inventory(ctx.materials) if getattr(ctx, "materials", None) else {}
    candidate = apply_reference_assignments(previous, pending, inventory)
    skill = next(s for s in await bundle.registry.list_visible() if s.name == "scheme_extract")
    Draft202012Validator(skill.output_schema).validate(candidate)
    candidate = validate_semantic_output(candidate, VisualExtraction)
    operations = jsonpatch.JsonPatch.from_diff(previous, candidate, optimization=False).patch
    working = copy.deepcopy(previous)
    changes = []
    for op in operations:
        try:
            before = _lookup(working, _parts(op["path"]))
        except ValueError:
            before = None
        changes.append(
            {"path": op["path"], "before": copy.deepcopy(before), "after": copy.deepcopy(op.get("value"))}
        )
        working = jsonpatch.JsonPatch([op]).apply(working)
    record = {
        "patch_id": f"sp{len(log.get('structure_patches', [])) + 1}",
        "kind": "reference_member_fields",
        "base_version": log["extraction_version"],
        "before_hash": fingerprint(previous),
        "after_hash": fingerprint(candidate),
        "supplements": copy.deepcopy(log["reference_assignments"]),
        "operations": operations,
        "changes": changes,
        "changed": bool(operations),
    }
    if not operations:
        log["applied_evidence_ids"] = pending["applied_evidence_ids"]
        log["reference_assignment_errors"] = pending["reference_assignment_errors"]
        return record
    state["extraction"] = candidate
    log["applied_evidence_ids"] = pending["applied_evidence_ids"]
    log["reference_assignment_errors"] = pending["reference_assignment_errors"]
    log.setdefault("structure_patches", []).append(record)
    log["extraction_version"] += 1
    log.pop("structure_audit", None)
    return record


def defer_delivery(state: dict) -> dict:
    """Retain ID-only output for debugging while performing no molecular assembly or persistence."""
    audit = state.get("exploration", {}).get("structure_audit", {})
    error = {
        "code": "structure_review_blocked",
        "field_path": "/pathways",
        "reason": "Independent structure review has not passed for this exact extraction",
        "audit": audit,
    }
    state["pre_backfill_extraction"] = copy.deepcopy(state["extraction"])
    state["backfilled_extraction"] = copy.deepcopy(state["extraction"])
    state["backfilled_extraction"]["status"] = "unclear"
    # Deliver the reviewed/ID-only flowchart instead of blocking the whole result.
    # Molecular backfill stays pending until an independent review passes.
    state["molecular_coverage"] = {
        "status": "pending_review",
        "delivery": "flowchart_only",
        "reason": "structure_review_not_passed",
        "note": "Flowchart extraction is delivered as ID-only; molecular backfill remains pending until an independent review passes.",
    }
    state["material"]["backfill_errors"] = [error]
    state["image_products"] = [
        p for p in state.get("image_products", []) if p.get("stage") == "model_input_numbered"
    ]
    return state
