"""Observable progress and edit recovery for any document-scoped figure task."""

from __future__ import annotations

import base64
import copy
import hashlib
import json
from typing import Literal

from jsonschema.exceptions import ValidationError as SchemaValidationError
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from drugpaper.agents.figure.context import pointer, uninvestigated_skill_gaps
from drugpaper.gateway.base import ImagePart, Message, TextPart

WRITES = frozenset(
    {
        "patch_structure",
        "record_reference_assignments",
        "repair_markush_cases",
        "apply_assembly",
        "regenerate_structure",
    }
)
PREVIEWS = {
    "preview_reference_assignments": "record_reference_assignments",
    "preview_structure_patch": "patch_structure",
}
DISCOVERY = frozenset(
    {
        "get_document_map",
        "get_outline",
        "preview_pages",
        "grep",
        "search",
        "search_text",
        "find_reference_visuals",
    }
)


class EditRejected(ValueError):
    def __init__(self, message: str, details: dict):
        super().__init__(message)
        self.details = details


class DeferEdit(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    edit_id: str
    disposition: Literal["withdrawn", "needs_evidence", "unsupported"]
    reason: str = Field(min_length=10, max_length=2000)
    evidence_ids: list[str] = Field(default_factory=list, max_length=12)


def defer_edit(log: dict, arguments: dict) -> dict:
    args = DeferEdit.model_validate(arguments)
    edit = next((e for e in log.get("pending_edits", []) if e["edit_id"] == args.edit_id), None)
    if edit is None or edit["status"] not in {"needs_correction", "ready_to_apply"}:
        raise ValueError("Only an existing unapplied edit can be withdrawn/deferred")
    if args.disposition != "withdrawn" and edit.get("attempts", 0) < 2:
        raise ValueError("Try correcting the rejected arguments before deferring an edit")
    if not set(args.evidence_ids) <= {e["id"] for e in log.get("evidence", [])}:
        raise ValueError("Use actual evidence IDs when documenting a deferred edit")
    edit.update(
        status=args.disposition, disposition_reason=args.reason, disposition_evidence_ids=args.evidence_ids
    )
    return {
        "ok": True,
        "edit_id": edit["edit_id"],
        "disposition": args.disposition,
        "note": "Proposal retained for review, never marked as applied; no extraction fields were changed",
    }


def digest(value) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False, default=str).encode()
    ).hexdigest()


def progress_signature(state: dict, validation: dict) -> str:
    log = state["exploration"]
    evidence = set()
    for item in log.get("evidence", []):
        result = item.get("result", {})
        images = result.get("image_refs", [])
        if images:
            evidence.add(digest([item["tool"], result.get("node_ref"), [r.get("blob_ref") for r in images]]))
        else:
            evidence.add(digest([item["tool"], result]))
    return digest(
        {
            "structure": state.get("extraction"),
            "evidence": sorted(evidence),
            "validation": {k: validation.get(k) for k in ("checks", "backfill_errors", "status")},
            "audit": log.get("structure_audit", {}).get("extraction_hash"),
            "validated_proposals": [
                {
                    "tool": e["tool"],
                    "arguments": {
                        k: v for k, v in e["arguments"].items() if k not in {"note", "reason", "relevance"}
                    },
                }
                for e in log.get("pending_edits", [])
                if e["status"] == "ready_to_apply"
            ],
        }
    )


def recovery_error(exc: Exception) -> dict:
    details = getattr(exc, "details", {})
    if isinstance(exc, ValidationError):
        details = {
            "code": "invalid_arguments",
            "fields": [
                {"path": "/" + "/".join(str(p) for p in e["loc"]), "message": e["msg"]}
                for e in exc.errors(include_input=False, include_url=False)[:20]
            ],
        }
        if any(
            phrase in str(exc)
            for phrase in (
                "numeric compound ranges require compound_series",
                "generic series with printed variables",
                "a series needs its printed reference",
            )
        ):
            details["action"] = (
                "Repair the same endpoint as a whole: KEEP its printed reference and scaffold_mol_id, set mol_id=null and substituents={}, add/retain compound_series with matching members/count. If variable_definitions are present, also add/retain markush description and variable_options. A reactant keeps a generic formula without expanded cases. Do not alternate deleting reference, compound_series or markush; they must coexist. The rejected patch changed nothing."
            )
    elif isinstance(exc, SchemaValidationError):
        leaves = []

        def collect(error):
            if error.context:
                for child in error.context:
                    collect(child)
            elif len(leaves) < 12:
                leaves.append(
                    {
                        "path": "/"
                        + "/".join(str(p).replace("~", "~0").replace("/", "~1") for p in error.absolute_path),
                        "message": error.message[:300],
                        "constraint": error.validator,
                        "allowed_values": error.validator_value if error.validator == "enum" else None,
                    }
                )

        collect(exc)
        details = {
            "code": "structure_schema_violation",
            "fields": leaves,
            "action": "Correct only the indicated structure fields using the listed allowed values/types. The whole patch was rolled back. Do not repeat a rejected enum value or regenerate the structure.",
        }
    return {
        "ok": False,
        "error": f"{type(exc).__name__}: {exc.message[:300]}"
        if isinstance(exc, SchemaValidationError)
        else f"{type(exc).__name__}: {exc}",
        "repair": details
        or {
            "code": "tool_rejected",
            "action": "Inspect the error and correct the affected arguments; do not repeat the unchanged call",
        },
    }


def track_edit(log: dict, name: str, arguments: dict, output: dict) -> None:
    target_tool = PREVIEWS.get(name, name)
    if target_tool not in WRITES:
        return
    operations = arguments.get("operations", [])
    operations = operations if isinstance(operations, list) else []
    target = (
        arguments.get("product_path")
        or tuple(op.get("path") for op in operations if isinstance(op, dict))
        or "current_structure"
    )
    key = digest([target_tool, target])
    entries = log.setdefault("pending_edits", [])
    item = next((e for e in entries if e["key"] == key), None)
    if item is None:
        if len(entries) >= 24:
            return
        item = {"edit_id": f"edit{len(entries) + 1}", "key": key, "tool": target_tool}
        entries.append(item)
    clean = {k: v for k, v in arguments.items() if k != "reason"}
    item.update(
        arguments=copy.deepcopy(clean),
        base_version=log.get("extraction_version"),
        status="needs_correction"
        if not output.get("ok")
        else "ready_to_apply"
        if name in PREVIEWS
        else "applied",
        attempts=item.get("attempts", 0) + 1,
    )
    if output.get("ok"):
        item.pop("repair", None)
        item.pop("error", None)
    else:
        item["repair"] = copy.deepcopy(output.get("repair", {}))
        item["error"] = output.get("error")


def workflow_status(state: dict, *, closing: bool = False) -> dict:
    log = state["exploration"]
    for edit in log.get("pending_edits", []):
        if edit["tool"] == "patch_structure" and edit["status"] in {"needs_correction", "ready_to_apply"}:
            operations = edit.get("arguments", {}).get("operations", [])
            operations = operations if isinstance(operations, list) else []
            # A large rejected patch may have been split into smaller commits.
            # Reconcile concrete field outcomes only; append indices, malformed
            # operations and test-only proposals require explicit disposition.
            concrete = [op for op in operations if isinstance(op, dict) and op.get("op") != "test"]
            if (
                concrete
                and len(concrete)
                == len([op for op in operations if not isinstance(op, dict) or op.get("op") != "test"])
                and all(
                    isinstance(op.get("path"), str)
                    and op["path"].startswith("/")
                    and not op["path"].endswith("/-")
                    and (
                        op.get("op") in {"add", "replace"}
                        and "value" in op
                        and pointer(state.get("extraction", {}), op["path"]) == op["value"]
                        or op.get("op") == "remove"
                        and pointer(state.get("extraction", {}), op["path"]) == {"$missing": True}
                    )
                    for op in concrete
                )
            ):
                edit["status"] = "satisfied_by_current_state"
        if (
            edit["tool"] != "record_reference_assignments"
            or edit["status"] not in {"needs_correction", "ready_to_apply"}
            or edit.get("repair", {}).get("code") == "invalid_arguments"
        ):
            continue
        args = edit["arguments"]
        product = pointer(state.get("extraction", {}), args.get("product_path", "/missing"))
        actual = (
            {m.get("reference"): m for m in product.get("variants", [])} if isinstance(product, dict) else {}
        )
        assignments = args.get("assignments", [])
        if assignments and all(
            a.get("reference") in actual
            and all(
                actual[a["reference"]].get("substituents", {}).get(k) == v
                for k, v in a.get("substituents", {}).items()
            )
            and (
                a.get("attachments") is None
                or set(a["attachments"]) == set(actual[a["reference"]].get("attachments", []))
            )
            and all(
                actual[a["reference"]].get("position_definitions", {}).get(k) == v
                for k, v in a.get("position_definitions", {}).items()
            )
            for a in assignments
        ):
            edit["status"] = "applied_via_patch"
    pending = [
        e for e in log.get("pending_edits", []) if e["status"] in {"needs_correction", "ready_to_apply"}
    ]
    targets = []
    seen = set()
    for gap in log.get("gaps", []):
        if gap["status"] == "resolved":
            continue
        for path in gap["field_paths"]:
            parent = path
            while parent:
                value = pointer(state.get("extraction", {}), parent)
                if isinstance(value, dict) and value.get("compound_series"):
                    if parent not in seen:
                        seen.add(parent)
                        members = set(value["compound_series"].get("members", []))
                        applied = {v.get("reference") for v in value.get("variants", [])}
                        targets.append(
                            {
                                "product_path": parent,
                                "missing_members": sorted(members - applied),
                                "applied_count": len(members & applied),
                                "all_member_references_present": members <= applied,
                                "series_structure_status": value["compound_series"].get("structure_status"),
                                "note": "Counts reflect written references only, not verified chemical identity or assembly success. Reconcile stale series status only after confirming all required site and position assignments.",
                            }
                        )
                    break
                parent = parent.rsplit("/", 1)[0]
    return {
        "mode": "close_pending_work" if closing else "explore_and_apply",
        "extraction_version": log.get("extraction_version", 0),
        "structure_review": {
            "status": log.get("structure_audit", {}).get("status"),
            "issues": log.get("structure_audit", {}).get("issues", []),
            "action": "When the structure is ready, call review_structure once. For needs_revision/uncertain, correct the reported paths using local edits, then review again; do not review after every edit and do not rely on a stale cached review.",
        },
        "pending_edits": [
            {k: e[k] for k in ("edit_id", "tool", "status", "arguments", "repair", "error") if k in e}
            for e in pending
        ],
        "deferred_edits": [
            {k: e[k] for k in ("edit_id", "tool", "status", "arguments", "disposition_reason") if k in e}
            for e in log.get("pending_edits", [])
            if e["status"] in {"needs_evidence", "unsupported", "withdrawn"}
        ],
        "member_targets": targets,
        "source_records": [
            {
                "supplement_id": r["supplement_id"],
                "product_path": r["product_path"],
                "members": [a["reference"] for a in r["assignments"]],
                "evidence_ids": [s["evidence_id"] for s in r["sources"]],
            }
            for r in log.get("reference_assignments", [])
            if r["assignments"]
        ],
        "editable_evidence": [
            {"id": e["id"], "tool": e.get("tool"), "node_ref": e.get("result", {}).get("node_ref")}
            for e in log.get("evidence", [])
            if e.get("tool") != "preview_pages"
            and (log.get("selected_evidence_ids") is None or e["id"] in log["selected_evidence_ids"])
        ],
        "gap_targets": [
            {
                **{k: g[k] for k in ("gap_id", "status", "question", "field_paths") if k in g},
                "still_reported_by_skill": any(
                    item.get("gap_id") == g["gap_id"].removeprefix("skill:")
                    for item in state.get("extraction", {}).get("information_gaps", [])
                ),
                "action": "Apply supported facts, validate_result, then update_gap(status=resolved) using this exact gap_id and applied evidence_ids. The host retires the original report after checking changed fields and affected validation errors; no separate deletion patch is needed. Otherwise record actual evidence and the remaining uncertainty. Do not resolve merely from member counts.",
            }
            for g in log.get("gaps", [])
            if g.get("status") != "resolved"
        ],
        "rule": "A read or gap note is not an applied result. Correct rejected edits using their exact field feedback, preview if useful, then commit supported partial facts. Unknown members remain unknown; do not invent assignments. In closing mode prioritise pending edits and validation; unfinished work is returned for review.",
    }


def ready_for_handoff(state: dict, validation: dict, *, validated: bool, reviewed: bool) -> bool:
    """A closing checkpoint, never a substitute for the finish tool's evidence gates."""
    log = state["exploration"]
    if not validated or not reviewed or workflow_status(state)["pending_edits"]:
        return False
    if validation.get("status") in {"failed", "not_a_table"} or uninvestigated_skill_gaps(log):
        return False
    gaps = log.get("gaps", [])
    known = {e["id"] for e in log.get("evidence", []) if e.get("tool") != "preview_pages"}
    for gap in gaps:
        if gap["status"] == "resolved":
            continue
        if (
            gap["status"] not in {"unresolved", "conflict"}
            or not gap.get("note", "").strip()
            or not gap.get("evidence_ids")
            or not set(gap["evidence_ids"]) <= known
        ):
            return False
    failures = validation.get("backfill_errors", [])
    if any(
        c.get("severity") == "error" and not (c.get("code") == "C_BACKFILL_ERR" and failures)
        for c in validation.get("checks", [])
    ):
        return False
    for failure in failures:
        # Symbolic candidates may remain explicitly unknown. Actual member failures
        # must continue through repair, regardless of a model's disposition note.
        if failure.get("code") != "markush_candidate_unresolved":
            return False
        try:
            from drugpaper.agents.figure.context import GapRequest

            path = GapRequest.normalize_paths([failure["field_path"]])[0]
        except (KeyError, ValueError):
            return False
        if not any(
            g["status"] in {"unresolved", "conflict"}
            and any(path == p or path.startswith(p + "/") for p in g["field_paths"])
            for g in gaps
        ):
            return False
    return True


class ProgressTracker:
    def __init__(self, state: dict, validation: dict):
        self.signature = progress_signature(state, validation)
        self.idle = 0
        self.finish_blocked_at = None
        self.correction_allowances = 2
        self.closed_gaps: set[str] = set()

    def allow_correction(self, name: str, output: dict) -> None:
        if (
            name in WRITES | set(PREVIEWS)
            and not output.get("ok")
            and output.get("repair", {}).get("code")
            in {
                "invalid_arguments",
                "missing_attachment_values",
                "attachment_site_mismatch",
                "misplaced_position_fields",
                "structure_schema_violation",
            }
            and self.correction_allowances > 0
        ):
            # Do not call a rejected proposal progress. Permit at most two
            # bounded recovery windows across this run, within global budgets.
            self.idle = 0
            self.correction_allowances -= 1

    def observe(self, state: dict, validation: dict) -> bool:
        current = progress_signature(state, validation)
        changed = current != self.signature
        # Each evidence-backed disposition earns one closing step per gap per run.
        # Editing notes or oscillating statuses cannot reset the stagnation budget.
        closed = {
            g["gap_id"]
            for g in state["exploration"].get("gaps", [])
            if g.get("status") in {"resolved", "unresolved", "conflict"}
            and g.get("evidence_ids")
            and g.get("note", "").strip()
        }
        if closed - self.closed_gaps:
            changed = True
            self.finish_blocked_at = None
        self.closed_gaps.update(closed)
        self.idle = 0 if changed else self.idle + 1
        self.signature = current
        return changed

    def can_finish(self) -> bool:
        return self.finish_blocked_at != self.signature


async def restore_evidence(
    store, log: dict, target_ref: str, max_images: int, max_bytes: int
) -> tuple[list[Message], dict]:
    """Reattach stored pixels, prioritising rejected edits; no new reads or domain facts."""
    priority = {
        eid
        for edit in log.get("pending_edits", [])
        if edit.get("status") in {"needs_correction", "ready_to_apply"}
        for eid in edit.get("arguments", {}).get("evidence_ids", [])
    }
    candidates = sorted(log.get("evidence", []), key=lambda e: e["id"] not in priority)
    messages, refs, omitted, failed = [], [], [], []
    seen = set()
    used = 0
    for evidence in candidates:
        result = evidence.get("result", {})
        if (
            evidence.get("tool") not in {"inspect_visual", "inspect_page", "inspect_crop"}
            or result.get("node_ref") == target_ref
        ):
            continue
        if result.get("node_ref") and not result["node_ref"].startswith(target_ref.rsplit("/", 1)[0] + "/"):
            failed.append(evidence["id"])
            continue
        for ref in result.get("image_refs", []):
            blob = ref.get("blob_ref")
            if not blob or blob in seen:
                continue
            seen.add(blob)
            if len(refs) >= max_images:
                omitted.append(evidence["id"])
                continue
            try:
                data = await store.get(blob)
            except (OSError, KeyError):
                failed.append(evidence["id"])
                continue
            encoded = base64.b64encode(data).decode()
            if used + len(encoded) > max_bytes:
                omitted.append(evidence["id"])
                continue
            metadata = {
                **ref,
                "evidence_id": evidence["id"],
                "node_ref": result.get("node_ref"),
                "role": "reference",
                "precision": "detail",
            }
            messages.append(
                Message(
                    role="user",
                    content=[
                        TextPart(
                            text="Restored saved evidence, not a new source inspection: "
                            + json.dumps(metadata, ensure_ascii=False)
                        ),
                        ImagePart(data_b64=encoded, blob_ref=blob, mime=ref.get("mime", "image/png")),
                    ],
                )
            )
            refs.append(metadata)
            used += len(encoded)
    return messages, {
        "kind": "evidence_restored",
        "image_refs": refs,
        "omitted_evidence_ids": sorted(set(omitted)),
        "unavailable_evidence_ids": sorted(set(failed)),
    }
