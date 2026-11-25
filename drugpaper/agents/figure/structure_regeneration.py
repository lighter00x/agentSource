"""Evidence-bound recovery from broadly incorrect initial scheme extraction."""

from __future__ import annotations

import copy
from typing import Literal

from jsonschema import Draft202012Validator
from pydantic import BaseModel, ConfigDict, Field

from drugpaper.agents.figure.context import extraction_evidence
from drugpaper.agents.figure.structure_review import MAX_REVIEWS, fingerprint
from drugpaper.facts.figure_models import VisualExtraction
from drugpaper.skills.output_integrity import reject_empty_replacement, validate_semantic_output


class RegenerateStructure(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    expected_version: int = Field(ge=1)
    damage_kind: Literal[
        "topology_error", "major_step_omissions", "widespread_placeholder_error", "template_mismatch"
    ]
    issue_ids: list[str] = Field(min_length=1, max_length=32)
    evidence_ids: list[str] = Field(min_length=1, max_length=12)
    note: str = Field(min_length=20, max_length=2000, description="解释整体错误及为何局部补丁不足")


async def regenerate_structure(agent, state: dict, arguments: dict, schema: dict) -> dict:
    args = RegenerateStructure.model_validate(arguments)
    log = state["exploration"]
    settings = agent.services.settings
    if "scheme_extract" not in state.get("chain", []) or not state.get("extraction"):
        raise ValueError("Regeneration requires an existing scheme extraction")
    if args.expected_version != log["extraction_version"]:
        raise ValueError("Stale structure version; use get_structure before regeneration")
    if len(log.get("structure_regenerations", [])) >= settings.figure_exploration_max_regenerations:
        raise ValueError("Structure regeneration budget exhausted; use local repair or leave for review")
    if log["extractions"] >= settings.figure_exploration_max_extractions:
        raise ValueError("extraction budget exhausted")
    if len(log.get("structure_reviews", [])) >= MAX_REVIEWS:
        raise ValueError("No independent review budget remains for regenerated structure")
    audit = log.get("structure_audit", {})
    if audit.get("status") != "needs_revision" or audit.get("extraction_hash") != fingerprint(
        state["extraction"]
    ):
        raise ValueError("Regeneration requires a current independent review identifying structure errors")
    issues = {i["issue_id"]: i for i in audit.get("issues", [])}
    if not set(args.issue_ids) <= issues.keys():
        raise ValueError("Unknown review issue ID")
    selected = [issues[i] for i in set(args.issue_ids) if issues[i].get("severity") == "error"]
    topology_failed = any(
        c.get("dimension") == "topology" and c.get("verdict") == "fail" for c in audit.get("coverage", [])
    )
    eligible = {
        "topology_error": any(i.get("category") == "incorrect_topology" for i in selected),
        "major_step_omissions": any(i.get("category") == "missing_step" for i in selected)
        and topology_failed,
        "widespread_placeholder_error": len(
            {i["field_path"] for i in selected if i.get("category") == "incorrect_placeholder"}
        )
        >= 2,
        "template_mismatch": any(i.get("category") == "other" for i in selected) and topology_failed,
    }
    if not eligible[args.damage_kind]:
        raise ValueError(
            "Review does not justify this broad regeneration; use patch_structure for local defects"
        )
    evidence = {e["id"] for e in extraction_evidence(log)}
    if not set(args.evidence_ids) <= evidence:
        raise ValueError(
            "Regeneration requires actual retained text/detail evidence, not preview or invented IDs"
        )

    previous = copy.deepcopy(state["extraction"])
    record = {
        "regeneration_id": f"regen{len(log.get('structure_regenerations', [])) + 1}",
        "base_version": args.expected_version,
        "damage_kind": args.damage_kind,
        "issue_ids": args.issue_ids,
        "evidence_ids": args.evidence_ids,
        "note": args.note,
        "before_hash": fingerprint(previous),
        "status": "running",
    }
    log.setdefault("structure_regenerations", []).append(record)
    log["extraction_attempts"] += 1
    # The worker sees all existing evidence and recognition, but failed generation cannot
    # mutate the active structure. Account for real calls even if the candidate is rejected.
    pending = copy.deepcopy(state)
    pending["exploration"]["regeneration_context"] = {
        "base_version": args.expected_version,
        "damage_kind": args.damage_kind,
        "note": args.note,
        "issues": [issues[i] for i in args.issue_ids],
        "instruction": "Rebuild a full candidate using current numbered images, placeholder IDs and retained source evidence; correct these broad errors without rerunning detection or recognition.",
    }
    try:
        await agent._structure_once(pending)
        candidate = pending["extraction"]
        # Broad topology corrections may shift array indices. Reuse confirmed source
        # assignments only when the family identity has one exact destination.
        references = pending["exploration"].get("reference_assignments", [])
        for source in references:
            matches = []
            for pi, pathway in enumerate(candidate.get("pathways", [])):
                for si, step in enumerate(pathway.get("steps", [])):
                    for mi, product in enumerate(step.get("products", [])):
                        if (product.get("reference"), product.get("scaffold_mol_id")) == (
                            source["target_reference"],
                            source["scaffold_mol_id"],
                        ):
                            matches.append(f"/pathways/{pi}/steps/{si}/products/{mi}")
            if len(matches) != 1:
                raise ValueError(
                    "Cannot uniquely reattach confirmed member evidence to regenerated topology; previous structure retained"
                )
            source["product_path"] = matches[0]
        if references:
            from drugpaper.agents.figure.reference_assignments import (
                apply_reference_assignments,
                supplement_errors,
            )

            pending["exploration"]["applied_evidence_ids"] = list(evidence)
            candidate = apply_reference_assignments(candidate, pending["exploration"])
            if supplement_errors(candidate, references):
                raise ValueError(
                    "Regenerated structure conflicts with confirmed member evidence; previous structure retained"
                )
        reject_empty_replacement(previous, candidate)
        Draft202012Validator(schema).validate(candidate)
        candidate = validate_semantic_output(candidate, VisualExtraction)
        if fingerprint(candidate) == fingerprint(previous):
            raise ValueError("Regeneration produced no change; previous structure retained")
    except BaseException as exc:
        record.update(status="failed", error=f"{type(exc).__name__}: {exc}")
        raise
    finally:
        for key in ("accounting", "trace", "artifact_ids"):
            if key in pending:
                state[key] = pending[key]
        # Skill execution updates the agent's emergency-report state. It must never
        # keep pointing at an uncommitted/invalid candidate after this tool returns.
        agent.progress = state
    log.setdefault("structure_versions", []).append(
        {
            "version": args.expected_version,
            "extraction": previous,
            "structure_audit": copy.deepcopy(audit),
            "applied_evidence_ids": copy.deepcopy(log.get("applied_evidence_ids", [])),
            "reference_assignments": copy.deepcopy(log.get("reference_assignments", [])),
            "superseded_by": record["regeneration_id"],
        }
    )
    state["extraction"] = candidate
    log["extraction_version"] += 1
    log["extractions"] += 1
    log["applied_evidence_ids"] = list(evidence)
    for key in ("reference_assignment_errors", "reference_assignments"):
        if key in pending["exploration"]:
            log[key] = pending["exploration"][key]
    log.pop("structure_audit", None)
    for key in (
        "pre_backfill_extraction",
        "backfilled_extraction",
        "molecular_coverage",
        "checks",
        "checklist",
        "unchecked",
        "review",
    ):
        state.pop(key, None)
    state.get("material", {}).pop("backfill_errors", None)
    for gap in log.get("gaps", []):
        if gap["status"] == "resolved":
            gap.update(status="open", note="Structure regenerated; validate this gap again")
    record.update(
        status="awaiting_review", new_version=log["extraction_version"], after_hash=fingerprint(candidate)
    )
    return record
