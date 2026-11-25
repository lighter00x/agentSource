"""Document-scoped, typed reference assignments; independent of particular paper labels."""

from __future__ import annotations

import copy
import json
import re
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from drugpaper.agents.figure.context import pointer
from drugpaper.agents.figure.member_normalization import normalization_checks
from drugpaper.agents.figure.workflow import EditRejected
from drugpaper.chem.assembly import normalize_group
from drugpaper.facts.compound_instances import CompoundInstance
from drugpaper.facts.compound_series import numeric_members


class ReferenceAssignment(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    reference: str = Field(
        min_length=1,
        max_length=120,
        description="One paper member identifier, not a detection ID or chemical position.",
    )
    substituents: dict[str, str] = Field(
        min_length=1,
        max_length=20,
        description="Source-supported group/atom identities keyed by image-derived variable or local group, e.g. XCHYZ=OCH2F or Y=N. Position-only values such as CHO=4 belong only in position_definitions; do not copy table position columns into this map.",
    )
    attachments: list[str] | None = Field(
        default=None,
        min_length=1,
        max_length=64,
        description="Select complete local assignment keys already in substituents for later assembly. Never add an existing scaffold group merely because a table gives its position; position_definitions keys need not appear here. No recognition-label mapping is required during structuring.",
    )
    position_definitions: dict[str, str] = Field(
        default_factory=dict,
        max_length=20,
        description="Optional source group/site to explicit location, e.g. CHO=4 or XCHYZ=2′. Leave empty when the source has no separate position data and identity is unambiguous; never invent locants. Preserve reported positions of existing scaffold groups even when absent from substituents/attachments, and keep primes exactly as printed. Put prose in evidence_text; omit redundant positions already included in a locanted group value.",
    )
    evidence_text: str = Field(
        min_length=1,
        max_length=2000,
        description="Actual source table/figure, member row, column meanings and visible values or uncertainty. Reviewer suggestions are not source evidence.",
    )


class RecordReferenceAssignments(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    product_path: str = Field(max_length=200)
    evidence_ids: list[str] = Field(min_length=1, max_length=12)
    relevance: str = Field(min_length=1, max_length=2000)
    assignments: list[ReferenceAssignment] = Field(min_length=1, max_length=32)
    supersedes: list[str] = Field(
        default_factory=list,
        max_length=16,
        description="Explicit prior supplement IDs whose overlapping members are corrected; unrelated members remain unchanged",
    )
    correction_reason: str = Field(
        default="",
        max_length=2000,
        description="Required for supersedes: explain the source-supported correction, not a silent overwrite",
    )


class ExplicitReferenceAssignment(ReferenceAssignment):
    attachments: list[str] = Field(
        min_length=1,
        max_length=64,
        description=ReferenceAssignment.model_fields["attachments"].description,
    )


class ExplicitRecordReferenceAssignments(RecordReferenceAssignments):
    assignments: list[ExplicitReferenceAssignment] = Field(min_length=1, max_length=32)


class FindReferenceVisuals(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    references: list[str] = Field(min_length=1, max_length=32)
    terms: list[str] = Field(default_factory=list, max_length=12)
    limit: int = Field(default=8, ge=1, le=12)


def visual_navigation_text(source, node) -> str:
    """Rank bounded captions, not an attached footnote or accidentally merged page."""
    text = source.text_of(node)
    text = re.split(r"\n\s*[a-z]\s+", text, maxsplit=1)[0][:600]
    # OCR sometimes separates the leading digit of a range; navigation only.
    return re.sub(r"(\d)\s+(\d+)(?=\s*[-–−—])", r"\1\2", text)


def find_reference_visuals(source: Any, target_ref: str, args: dict) -> dict:
    """Rank real caption/label matches; do not infer that a matched source answers the gap."""
    req = FindReferenceVisuals.model_validate(args)
    wanted = set()
    for label in req.references:
        wanted.update(numeric_members(label) or [label])
    result = []
    for node in source.ir.nodes:
        if node.status == "removed" or node.node_type not in {
            "figure",
            "table",
            "scheme",
            "figure_part",
            "chart_panel",
        }:
            continue
        ref = source.ref(node.local_id)
        if ref == target_ref:
            continue
        text = visual_navigation_text(source, node).strip()
        search_text = re.sub(r"\b(?:Fig(?:ure)?\.?|Scheme|Table)\s*S?\d+[A-Za-z]?\b", "", text, flags=re.I)
        found = set()
        # Bound range expansion. Numbers alone are weak matches; prefer explicit ranges/caption terms.
        for match in re.finditer(r"(?<![\d.])\d+\s*[-–−—]\s*\d+(?!\d)", search_text):
            found.update(numeric_members(match[0]) or [])
        found.update(
            label
            for label in wanted
            if re.search(r"(?<![\w.])" + re.escape(label) + r"(?![\w.])", search_text)
        )
        overlap = wanted & found
        hits = [
            t
            for t in req.terms
            if (
                bool(re.search(r"(?<!\w)" + re.escape(t) + r"(?!\w)", search_text, re.I))
                if len(t) <= 3
                else t.casefold() in search_text.casefold()
            )
        ]
        if not overlap and not hits:
            continue
        result.append(
            {
                "node_id": ref,
                "label": node.label,
                "type": node.node_type,
                "caption_preview": text[:600],
                "pages": [r.page for r in node.geometry.regions],
                "matched_references": sorted(overlap),
                "matched_terms": hits,
                "reference_coverage": len(overlap) / max(len(wanted), 1),
                "score": 4 * len(overlap) / max(len(wanted), 1) + len(hits),
            }
        )
    result.sort(key=lambda v: (-v["score"], v["node_id"]))
    return {
        "ok": True,
        "candidates": result[: req.limit],
        "note": "Caption matches are navigation only. Inspect actual figures/pages and justify correspondence before recording assignments.",
    }


def record_reference_assignments(
    source: Any, state: dict, args: dict, *, correction_prepared: bool = False
) -> dict:
    req = RecordReferenceAssignments.model_validate(args)
    if req.supersedes and not correction_prepared:
        raise ValueError("Reference corrections require the independently reviewed edit transaction")
    if not req.relevance.strip():
        raise ValueError("Explain the observed relation between source and target members")
    if not re.fullmatch(
        r"/pathways/(?:0|[1-9][0-9]*)/steps/(?:0|[1-9][0-9]*)/products/(?:0|[1-9][0-9]*)", req.product_path
    ):
        raise ValueError("Reference supplementation must target an existing product endpoint")
    target = pointer(state.get("extraction", {}), req.product_path)
    scaffold = target.get("scaffold_mol_id")
    members = set(target.get("compound_series", {}).get("members", []))
    if type(scaffold) is not int or not members:
        raise ValueError("Target must have a current-figure scaffold and declared members")
    checks = normalization_checks(target, [a.model_dump() for a in req.assignments])
    conflicts = [c for c in checks if c["status"] == "inconsistent"]
    if conflicts:
        raise EditRejected(
            "Member normalization contradicts the original local expression or variable assignments",
            {
                "code": "member_normalization_conflict",
                "product_path": req.product_path,
                "conflicts": conflicts,
                "action": "The LLM must recheck and correct the normalized local group, preserving original variables, positions and evidence. Resubmit the full intended batch after correcting these fields. Do not move internal variables into separate attachments or change source values to fit the result. For already registered members use supersedes and correction_reason; do not retry assembly with conflicting values.",
            },
        )
    log = state["exploration"]
    by_id = {e["id"]: e for e in log.get("evidence", [])}
    sources = []
    for eid in req.evidence_ids:
        evidence = by_id.get(eid)
        if not evidence or evidence.get("tool") not in {"inspect_visual", "inspect_page", "inspect_crop"}:
            raise ValueError(
                "Actual detailed visual evidence is required; searches/previews alone cannot establish image assignments"
            )
        result = evidence.get("result", {})
        node_id = result.get("node_ref") or evidence.get("arguments", {}).get("node_id")
        if node_id:
            node = source.node(node_id)
            node_id = source.ref(node.local_id)
            if node_id == state["node_ref"]:
                raise ValueError(
                    "Reference supplementation requires related visual evidence, not only the current figure"
                )
        elif evidence.get("tool") != "inspect_page":
            raise ValueError("Missing source node")
        if not result.get("image_refs"):
            raise ValueError("Source image was not actually provided")
        sources.append(
            {
                "evidence_id": eid,
                "node_ref": node_id,
                "pages": sorted({im["page"] for im in result["image_refs"]}),
            }
        )
    seen = set()
    for index, assignment in enumerate(req.assignments):
        if assignment.reference not in members or assignment.reference in seen:
            raise ValueError(
                "Assignments must name distinct existing members, not variable combinations or other series"
            )
        seen.add(assignment.reference)
        missing = sorted(set(assignment.attachments or []) - assignment.substituents.keys())
        if missing:
            raise EditRejected(
                "Selected attachment keys must also exist in substituents with their complete local group values",
                {
                    "code": "missing_attachment_values",
                    "path": f"/assignments/{index}/substituents",
                    "missing_keys": missing,
                    "action": "Preserve the internal variable fields. Add each selected actual-site key with its evidence-supported complete group value. Read the product Markush cases/current inventory if needed; do not change the selector to an internal variable.",
                },
            )
        CompoundInstance(
            reference=assignment.reference,
            scaffold_mol_id=scaffold,
            substituents=assignment.substituents,
            attachments=assignment.attachments,
            position_definitions=assignment.position_definitions,
            evidence_text=assignment.evidence_text,
        )
    records = log.setdefault("reference_assignments", [])
    record = {
        "supplement_id": f"ra{len(records) + 1}",
        "product_path": req.product_path,
        "target_reference": target.get("reference"),
        "scaffold_mol_id": scaffold,
        "sources": sources,
        "relevance": req.relevance,
        "assignments": [a.model_dump() for a in req.assignments],
        "supersedes": list(req.supersedes),
        "correction_reason": req.correction_reason,
        "normalization_checks": checks,
    }
    # A contradictory second record requires explicit review instead of silently superseding evidence.
    for prior in records:
        if prior["product_path"] == req.product_path:
            if prior["sources"] == record["sources"] and prior["assignments"] == record["assignments"]:
                return {
                    "ok": True,
                    "record": copy.deepcopy(prior),
                    "reused": True,
                    "next_step": "apply_reference_patch",
                }
            old = {a["reference"]: a for a in prior["assignments"]}
            for a in record["assignments"]:
                if a["reference"] in old and any(
                    a.get(k) != old[a["reference"]].get(k)
                    for k in ["substituents", "position_definitions", "attachments"]
                ):
                    raise ValueError(
                        f"Conflicting reference assignment for {a['reference']} in {prior['supplement_id']}; "
                        "inspect the actual source again, then submit a reviewed correction with "
                        f"supersedes=['{prior['supplement_id']}'] and correction_reason. "
                        "Keep unrelated members and do not silently overwrite source facts."
                    )
    if len(records) >= 16:
        raise ValueError("Reference assignment record budget exhausted")
    records.append(copy.deepcopy(record))
    return {
        "ok": True,
        "record": record,
        "next_step": "apply_reference_patch",
        "note": "Reference information is recorded separately; apply the member fields locally, never regenerate the figure.",
    }


async def edit_reference_assignments(
    agent, source, state: dict, args: dict, *, preview: bool = False
) -> dict:
    """Register and apply in one transaction; no partial source ledger on schema failure."""
    from drugpaper.agents.figure.structure_review import apply_reference_patch

    # Historical source ledgers remain readable; new transactional writes must
    # supply assembly selectors instead of deferring this failure to review.
    request = ExplicitRecordReferenceAssignments.model_validate(args)
    target = pointer(state["extraction"], request.product_path)
    for index, assignment in enumerate(request.assignments):
        drawn = next(
            (
                v
                for v in target.get("variants", [])
                if v.get("reference") == assignment.reference and v.get("mol_id") is not None
            ),
            None,
        )
        if drawn:
            raise EditRejected(
                "This member already has its own recognized structure in the current figure",
                {
                    "code": "direct_member_already_recognized",
                    "reference": assignment.reference,
                    "mol_id": drawn["mol_id"],
                    "path": f"/assignments/{index}",
                    "action": "Keep the directly drawn member unchanged. Submit source assignments only for members still requiring scaffold assembly; do not overwrite a recognized full molecule. A verified direct member also contributes to series completeness.",
                },
            )
        misplaced = [
            key
            for key in assignment.substituents
            if re.search(r"(?:\bposition\b|位置|位点)", key, re.IGNORECASE)
            and key not in assignment.position_definitions
        ]
        if misplaced:
            raise EditRejected(
                "Position metadata must be retained in position_definitions before assembly",
                {
                    "code": "misplaced_position_fields",
                    "path": f"/assignments/{index}/position_definitions",
                    "fields": misplaced,
                    "action": "Preserve each reported source position key and its exact supported value in position_definitions. Keep attachments as site keys only; do not encode positions into attachment names or discard any source identity fields.",
                },
            )
    pending = copy.deepcopy(state)
    if request.supersedes:
        prepare_reference_correction(pending, request)
    output = record_reference_assignments(source, pending, args, correction_prepared=bool(request.supersedes))
    output["patch"] = await apply_reference_patch(agent, pending)
    if preview:
        return {
            **output,
            "dry_run": True,
            "next_step": "record_reference_assignments",
            "extraction_version": state["exploration"]["extraction_version"],
            "note": "Preview only; record_reference_assignments commits these fields and triggers independent review",
        }
    source_review = await agent._review_reference_assignments(state, args)
    review_stamp = {k: source_review[k] for k in ("status", "fingerprint") if k in source_review}
    output["record"]["source_review"] = review_stamp
    for record in pending["exploration"]["reference_assignments"]:
        if record["supplement_id"] == output["record"]["supplement_id"]:
            record["source_review"] = copy.deepcopy(review_stamp)
    state["extraction"] = pending["extraction"]
    for key in (
        "reference_assignments",
        "reference_assignment_revisions",
        "structure_patches",
        "applied_evidence_ids",
        "reference_assignment_errors",
        "extraction_version",
    ):
        if key in pending["exploration"]:
            state["exploration"][key] = pending["exploration"][key]
    if output["patch"].get("changed", True):
        state["exploration"].pop("structure_audit", None)
    return {
        **output,
        "source_review": source_review,
        "extraction_version": state["exploration"]["extraction_version"],
    }


def reconcile_drawn_source_records(extraction: dict, log: dict, inventory: dict) -> list[dict]:
    """Migrate old assembly constraints on drawn molecules to retained observations."""
    recognized = {s["mol_id"] for s in inventory.get("structures", []) if s.get("recognized")}
    changes = []
    for record in log.get("reference_assignments", []):
        product = pointer(extraction, record["product_path"])
        drawn = {
            v.get("reference"): v["mol_id"]
            for v in product.get("variants", [])
            if v.get("mol_id") in recognized
            and v.get("scaffold_mol_id") is None
            and not v.get("substituents")
        }
        active = []
        for assignment in record["assignments"]:
            ref = assignment["reference"]
            if ref not in drawn:
                active.append(assignment)
                continue
            observation = {
                "assignment": copy.deepcopy(assignment),
                "recognized_mol_id": drawn[ref],
                "binding": "observation_only",
                "reason": "Current-figure drawn structure remains primary; this source assignment must not force scaffold assembly",
            }
            record.setdefault("observations", []).append(observation)
            changes.append(
                {
                    "supplement_id": record["supplement_id"],
                    "product_path": record["product_path"],
                    "reference": ref,
                    "recognized_mol_id": drawn[ref],
                    "sources": copy.deepcopy(record["sources"]),
                }
            )
        record["assignments"] = active
    return changes


def prepare_reference_correction(state: dict, request: RecordReferenceAssignments) -> None:
    """Stage explicit member revisions on a transaction copy; source review gates commit."""
    if not request.correction_reason.strip():
        raise ValueError("supersedes requires a source-supported correction_reason")
    records = state["exploration"].get("reference_assignments", [])
    if any(
        r["product_path"] == request.product_path
        and r.get("supersedes") == request.supersedes
        and r["assignments"] == [a.model_dump() for a in request.assignments]
        and {s["evidence_id"] for s in r["sources"]} == set(request.evidence_ids)
        for r in records
    ):
        return
    selected = [r for r in records if r["supplement_id"] in request.supersedes]
    refs = {a.reference for a in request.assignments}
    if len(selected) != len(set(request.supersedes)) or any(
        r["product_path"] != request.product_path
        or not refs.intersection(a["reference"] for a in r["assignments"])
        for r in selected
    ):
        raise ValueError("supersedes must identify existing supplements for these same product members")
    history = state["exploration"].setdefault("reference_assignment_revisions", [])
    for record in selected:
        old = [a for a in record["assignments"] if a["reference"] in refs]
        history.append(
            {
                "supplement_id": record["supplement_id"],
                "product_path": request.product_path,
                "assignments": copy.deepcopy(old),
                "sources": copy.deepcopy(record["sources"]),
                "correction_reason": request.correction_reason,
            }
        )
        record["assignments"] = [a for a in record["assignments"] if a["reference"] not in refs]


def supplement_errors(extraction: dict, records: list[dict]) -> list[dict]:
    errors = []
    for record in records:
        product = pointer(extraction, record["product_path"])
        if (
            product.get("scaffold_mol_id") != record["scaffold_mol_id"]
            or product.get("reference") != record["target_reference"]
        ):
            errors.append(
                {
                    "code": "reference_target_changed",
                    "field_path": record["product_path"],
                    "supplement_id": record["supplement_id"],
                }
            )
            continue
        variants = {v.get("reference"): v for v in product.get("variants", [])}
        from drugpaper.chem.assembly_inputs import preserves_assignment

        attachment_labels = list(
            dict.fromkeys(
                c["expression"] for c in (product.get("markush") or {}).get("substitution_cases", [])
            )
        )
        for item in record["assignments"]:
            instance = variants.get(item["reference"])
            if not instance:
                errors.append(
                    {
                        "code": "reference_member_not_applied",
                        "field_path": record["product_path"] + "/variants",
                        "reference": item["reference"],
                    }
                )
                continue
            if (
                instance.get("mol_id") is not None
                or instance.get("scaffold_mol_id") != record["scaffold_mol_id"]
            ):
                errors.append(
                    {
                        "code": "reference_structure_source_conflict",
                        "field_path": record["product_path"],
                        "reference": item["reference"],
                    }
                )
            if preserves_assignment(attachment_labels, item, instance):
                continue
            if item.get("attachments") is not None and set(item["attachments"]) != set(
                instance.get("attachments") or []
            ):
                errors.append(
                    {
                        "code": "reference_attachment_selection_not_preserved",
                        "field_path": record["product_path"] + "/variants",
                        "reference": item["reference"],
                    }
                )
            for key in ["substituents", "position_definitions"]:
                expected = item[key]
                observed = instance.get(key, {})
                if any(
                    k not in observed
                    or (normalize_group(observed[k]) if isinstance(observed[k], str) else observed[k])
                    != normalize_group(v)
                    for k, v in expected.items()
                ):
                    errors.append(
                        {
                            "code": "reference_assignment_not_preserved",
                            "field_path": record["product_path"] + "/variants",
                            "reference": item["reference"],
                            "required_field": key,
                            "supplement_id": record["supplement_id"],
                        }
                    )
    return errors


def apply_reference_assignments(extraction: dict, log: dict, inventory: dict | None = None) -> dict:
    """Fill missing confirmed fields without silently overriding contradictory new extraction."""
    output = copy.deepcopy(extraction)
    errors = []
    applied = set(log.get("applied_evidence_ids", []))
    for record in log.get("reference_assignments", []):
        if not {s["evidence_id"] for s in record["sources"]} <= applied:
            errors.append(
                {"code": "reference_evidence_not_applied", "supplement_id": record["supplement_id"]}
            )
            continue
        product = pointer(output, record["product_path"])
        if (
            product.get("scaffold_mol_id") != record["scaffold_mol_id"]
            or product.get("reference") != record["target_reference"]
        ):
            errors.append({"code": "reference_target_changed", "field_path": record["product_path"]})
            continue
        # pointer returns a copy; mutate only this verified endpoint after checking its identity.
        variants = product.setdefault("variants", [])
        for a in record["assignments"]:
            member = next((v for v in variants if v.get("reference") == a["reference"]), None)
            if member is None:
                member = {
                    "reference": a["reference"],
                    "scaffold_mol_id": record["scaffold_mol_id"],
                    "substituents": {},
                    "evidence_text": a["evidence_text"],
                }
                variants.append(member)
            if member.get("mol_id") is not None or member.get("scaffold_mol_id") != record["scaffold_mol_id"]:
                errors.append(
                    {
                        "code": "reference_structure_source_conflict",
                        "field_path": record["product_path"],
                        "reference": a["reference"],
                    }
                )
                continue
            if record.get("supersedes"):
                # Explicit, independently audited revision; unrelated members and
                # the recognized scaffold remain untouched. The patch records
                # the actual old values, while source history retains evidence.
                for key in ("substituents", "position_definitions", "attachments"):
                    if key in a:
                        member[key] = copy.deepcopy(a[key])
                member["evidence_text"] = a["evidence_text"]
            for key in ["substituents", "position_definitions"]:
                current = member.setdefault(key, {})
                for name, value in a[key].items():
                    if name in current and normalize_group(str(current[name])) != normalize_group(value):
                        errors.append(
                            {
                                "code": "reference_assignment_conflict",
                                "field_path": record["product_path"],
                                "reference": a["reference"],
                                "field": name,
                            }
                        )
                    else:
                        current[name] = value
            if a.get("attachments") is not None:
                if member.get("attachments") is not None and set(member["attachments"]) != set(
                    a["attachments"]
                ):
                    errors.append(
                        {
                            "code": "reference_attachment_selection_conflict",
                            "field_path": record["product_path"],
                            "reference": a["reference"],
                        }
                    )
                else:
                    member["attachments"] = list(a["attachments"])
            note = f"Reference supplement {record['supplement_id']}: " + a["evidence_text"]
            if note not in member.get("evidence_text", ""):
                member["evidence_text"] = member.get("evidence_text", "") + "; " + note
        if variants and product.get("compound_series", {}).get("structure_status") == "unresolved":
            product["compound_series"]["structure_status"] = "partial"
        parent = output
        parts = record["product_path"][1:].split("/")
        for key in parts[:-1]:
            parent = parent[int(key)] if isinstance(parent, list) else parent[key]
        parent[int(parts[-1])] = product
    errors.extend(supplement_errors(output, log.get("reference_assignments", [])))
    # Complete source assignments do not imply successful molecular assembly.
    # Real commits additionally require the independent source audit to pass.
    sites = {
        s["mol_id"]: set(s.get("attachment_labels", [])) for s in (inventory or {}).get("structures", [])
    }
    for record in log.get("reference_assignments", []):
        path = record["product_path"]
        product = pointer(output, path)
        series = product.get("compound_series", {})
        members = set(series.get("members", []))
        actual = {v.get("reference"): v for v in product.get("variants", [])}
        confirmed = {
            a["reference"]
            for r in log["reference_assignments"]
            if r["product_path"] == path
            for a in r["assignments"]
        }
        recognized = {s["mol_id"] for s in (inventory or {}).get("structures", []) if s.get("recognized")}
        drawn = {
            ref
            for ref, value in actual.items()
            if value.get("mol_id") in recognized
            and value.get("scaffold_mol_id") is None
            and not value.get("substituents")
        }
        confirmed |= drawn
        expected = sites.get(product.get("scaffold_mol_id"), set())
        if (
            members
            and members <= actual.keys()
            and members <= confirmed
            and all(
                ref in drawn
                or (
                    bool(actual[ref].get("attachments"))
                    and (not expected or set(actual[ref]["attachments"]) == expected)
                    and set(actual[ref]["attachments"]) <= actual[ref].get("substituents", {}).keys()
                )
                for ref in members
            )
            and not any(e.get("field_path", "").startswith(path) for e in errors)
        ):
            parent = output
            parts = path[1:].split("/")
            for key in parts[:-1]:
                parent = parent[int(key)] if isinstance(parent, list) else parent[key]
            parent[int(parts[-1])]["compound_series"].update(
                structure_status="resolved", unresolved_reason=""
            )
    log["reference_assignment_errors"] = errors
    if errors or any(
        pointer(output, r["product_path"]).get("compound_series", {}).get("structure_status") != "resolved"
        for r in log.get("reference_assignments", [])
    ):
        output["status"] = "unclear"
    return output


def reference_constraint_errors(extraction: dict, log: dict) -> list[dict]:
    errors = supplement_errors(extraction, log.get("reference_assignments", []))
    applied = set(log.get("applied_evidence_ids", []))
    for record in log.get("reference_assignments", []):
        if not {s["evidence_id"] for s in record["sources"]} <= applied:
            errors.append(
                {
                    "code": "reference_evidence_not_applied",
                    "field_path": record["product_path"],
                    "supplement_id": record["supplement_id"],
                }
            )
    errors.extend(
        {
            "code": "member_identity_not_established",
            "field_path": p,
            "reason": "Distinct references share the same template assignment; locate actual member-specific information and record its evidence, or explicitly verify identity.",
        }
        for p in unproven_member_paths(extraction, log.get("reference_assignments", []))
    )
    return errors


def enforce_reference_assignments(delivery: dict, extraction: dict, log: dict) -> None:
    """A computed molecule cannot be accepted if registered source constraints were lost."""
    errors = reference_constraint_errors(extraction, log)
    log["reference_assignment_errors"] = copy.deepcopy(errors)
    if not errors:
        return
    seen = set()
    for error in errors:
        identity = str(error)
        if identity in seen:
            continue
        seen.add(identity)
        delivery["errors"].append(error)
        for record in log.get("reference_assignments", []):
            if error.get("field_path") and not error["field_path"].startswith(record["product_path"]):
                continue
            product = pointer(delivery["payload"], record["product_path"])
            if "$missing" in product:
                continue
            for member in product.get("variants", []):
                if error.get("reference") and member.get("reference") != error["reference"]:
                    continue
                member.pop("assembled_molecule", None)
                member.pop("assembly_provenance", None)
                member["assembly_status"] = "needs_review"
            parent = delivery["payload"]
            parts = record["product_path"][1:].split("/")
            for part in parts[:-1]:
                parent = parent[int(part)] if isinstance(parent, list) else parent[part]
            if "$missing" not in product:
                parent[int(parts[-1])] = product
    delivery["payload"]["status"] = "unclear"
    delivery["coverage"]["status"] = "needs_review"


def blocked_instance_paths(extraction: dict, log: dict) -> list[str]:
    errors = reference_constraint_errors(extraction, log)
    blocked = set()
    for pointer_path in unproven_member_paths(extraction, log.get("reference_assignments", [])):
        blocked.add(
            "$" + "".join("[" + c + "]" if c.isdigit() else "." + c for c in pointer_path[1:].split("/"))
        )
    for record in log.get("reference_assignments", []):
        relevant = [
            e for e in errors if not e.get("field_path") or e["field_path"].startswith(record["product_path"])
        ]
        product = pointer(extraction, record["product_path"])
        for i, member in enumerate(product.get("variants", [])):
            if any(not e.get("reference") or e["reference"] == member.get("reference") for e in relevant):
                components = record["product_path"][1:].split("/") + ["variants", str(i)]
                blocked.add("$" + "".join("[" + c + "]" if c.isdigit() else "." + c for c in components))
    return sorted(blocked)


def unproven_member_paths(extraction: dict, records: list[dict]) -> list[str]:
    """Repeated template assignments across distinct labels need member evidence, not duplication."""
    proven = {(r["product_path"], a["reference"]) for r in records for a in r["assignments"]}
    missing = []
    for pi, pw in enumerate(extraction.get("pathways", [])):
        for si, step in enumerate(pw.get("steps", [])):
            for ei, product in enumerate(step.get("products", [])):
                if not product.get("compound_series"):
                    continue
                path = f"/pathways/{pi}/steps/{si}/products/{ei}"
                groups = {}
                for vi, member in enumerate(product.get("variants", [])):
                    if member.get("scaffold_mol_id") is None:
                        continue
                    # Explicit R/S labels are handled by the established stereochemistry resolver.
                    stereo = re.match(r"^\(([RS])\)-", member.get("reference", ""))
                    key = json.dumps(
                        [
                            member.get("scaffold_mol_id"),
                            {
                                k: normalize_group(v) if isinstance(v, str) else v
                                for k, v in member.get("substituents", {}).items()
                            },
                            member.get("position_definitions", {}),
                            stereo[1] if stereo else None,
                        ],
                        sort_keys=True,
                    )
                    groups.setdefault(key, []).append((vi, member.get("reference")))
                if any(len({ref for _, ref in matches}) > 1 for matches in groups.values()):
                    # A collapsed dimension may affect the whole series, including otherwise unique groups.
                    for vi, member in enumerate(product.get("variants", [])):
                        if (
                            member.get("scaffold_mol_id") is not None
                            and (path, member.get("reference")) not in proven
                        ):
                            missing.append(path + f"/variants/{vi}")
    return missing
