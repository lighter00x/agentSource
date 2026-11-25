"""Bounded, traceable coarse-to-fine evidence context for one FigureAgent."""

from __future__ import annotations

import copy
import json
import re
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from drugpaper.gateway.base import ChatRequest, ImagePart, Message, TextPart

DETAIL_TOOLS = frozenset({"inspect_visual", "inspect_page", "inspect_crop"})


def structuring_view(value: Any) -> Any:
    """Model-facing ID-only view; preserve document prose and symbolic substituents."""
    hidden = {
        "smiles",
        "esmiles",
        "e_smiles_raw",
        "smiles_core",
        "scaffold_esmiles",
        "esmiles_list",
        "filled_substituents",
        "molecular_bindings",
        "backfilled_substituents",
        "molecular_structure",
        "scaffold_structure",
        "reference_binding",
        "instance_structure",
        "molecule_objects",
        "canonical_smiles",
        "formula",
        "inchi",
        "inchikey",
        "structures",
        "assembled_molecule",
        "assembly_provenance",
        "assembly_candidates",
        "candidate_smiles",
        "attachment_labels",
        "variable_annotations",
    }
    if isinstance(value, dict):
        return {k: structuring_view(v) for k, v in value.items() if str(k).lower() not in hidden}
    if isinstance(value, list):
        return [structuring_view(v) for v in value]
    return value


def current_structure_inventory(materials: Any) -> dict:
    """Expose current-box IDs and attachment names, never recognition chemical strings."""
    target = materials.target
    children = {n["ref"]: n for n in target.get("children", []) if n.get("status") != "removed"}
    records = []
    for structure in target.get("structures", []):
        child = children.get(structure.get("node_ref"))
        if not child or type(child.get("attributes", {}).get("reading_index")) is not int:
            continue
        labels = re.findall(r"<a>[0-9]+:([^<]+)</a>", structure.get("e_smiles_raw") or "")
        annotations = [
            {"kind": kind, "label": label}
            for kind, label in re.findall(r"<([ar])>[0-9]+:([^<]+)</\1>", structure.get("e_smiles_raw") or "")
            if len(label) <= 80
        ]
        records.append(
            {
                "mol_id": child["attributes"]["reading_index"],
                "recognized": bool(structure.get("validation", {}).get("valid")),
                "attachment_labels": list(dict.fromkeys(label for label in labels if len(label) <= 80)),
                "variable_annotations": annotations,
            }
        )
    return {
        "scope": "current_figure",
        "target_ref": target.get("ref"),
        "structures": records,
        "rule": "Current figure recognition supplies the scaffold. Verified related visuals may supplement substituent, member and position definitions with provenance; their detection IDs are separate.",
    }


def structure_placeholder_inventory(materials: Any) -> dict:
    """Structure extraction owns image-to-ID references, not recognition-site mapping."""
    inventory = current_structure_inventory(materials)
    return {
        "scope": inventory["scope"],
        "target_ref": inventory["target_ref"],
        "structures": [
            {"mol_id": s["mol_id"], "recognized": s["recognized"]} for s in inventory["structures"]
        ],
        "rule": "Use numbered images to extract placeholders, visible variables and candidate expressions. Recognition attachment labels are deliberately withheld; mapping and scaffold compatibility are checked during backfill/assembly.",
    }


class ContextBudgetError(ValueError):
    """Target or retained tool history cannot fit without discarding required material."""


class GapUpdateRejected(ValueError):
    def __init__(self, message: str, *, code: str, action: str, gap_id: str):
        super().__init__(message)
        self.details = {"code": code, "action": action, "gap_id": gap_id}


class MapRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    offset: int = Field(default=0, ge=0)
    limit: int = Field(default=12, ge=1, le=24)


class PreviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    pages: list[int] = Field(min_length=1, max_length=6)
    gap_ids: list[str] = Field(default_factory=list, max_length=24)


class GapRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    gap_id: str = Field(min_length=1, max_length=80)
    question: str | None = Field(
        default=None, min_length=1, max_length=2000, description="新建必填；更新已有gap时省略，保留原问题"
    )
    field_paths: list[str] | None = Field(
        default=None,
        min_length=1,
        max_length=20,
        description="Affected extraction fields; prefer JSON pointers /pathways/0/steps/0/products. Also accepts simple validator paths $.pathways[0].steps[0].products; no wildcards or expressions.",
    )
    status: str = "open"
    evidence_ids: list[str] = Field(default_factory=list, max_length=40)
    note: str = Field(default="", max_length=3000)

    @field_validator("field_paths")
    @classmethod
    def normalize_paths(cls, paths: list[str] | None) -> list[str] | None:
        if paths is None:
            return None
        normalized = []
        for path in paths:
            if path.startswith("$"):
                if not re.fullmatch(r"\$(?:\.[A-Za-z_][A-Za-z0-9_]*|\[(?:0|[1-9][0-9]*)\])+", path):
                    raise ValueError("Only concrete field/index paths are allowed, no wildcard or expression")
                keys = re.findall(r"\.([A-Za-z_][A-Za-z0-9_]*)|\[([0-9]+)\]", path)
                path = "/" + "/".join(name or index for name, index in keys)
            if not path.startswith("/") or path == "/":
                raise ValueError("Use a non-root JSON pointer /pathways/0 or simple $.pathways[0] path")
            normalized.append(path)
        return list(dict.fromkeys(normalized))


def document_map(source: Any, offset: int = 0, limit: int = 12) -> dict:
    """Page pagination makes visuals beyond the initial 100 discoverable."""
    pages = sorted(source.pages())
    records = []
    for page in pages[offset : offset + limit]:
        nodes = [n for n in source.page_nodes(page) if n.status != "removed"]
        visuals = [
            n for n in nodes if n.node_type in {"figure", "scheme", "table", "chart_panel", "figure_part"}
        ]
        records.append(
            {
                "page": page,
                "display_page": page + 1,
                "printed_page": None,
                "sections": sorted({n.section_role for n in nodes if n.section_role}),
                "text_available": any(source.text_of(n) for n in nodes),
                "text_preview": " ".join(
                    source.text_of(n) for n in nodes if n.node_type in {"paragraph", "heading"}
                )[:500],
                "visuals": [
                    {
                        "node_id": source.ref(n.local_id),
                        "label": n.label,
                        "type": n.node_type,
                        "caption_preview": source.text_of(n)[:240],
                        "pages": [r.page for r in n.geometry.regions],
                        "continuations": [
                            source.ref(r.target)
                            for r in source.ir.relations
                            if r.source == n.local_id and r.type == "continuation" and r.status != "removed"
                        ],
                    }
                    for n in visuals
                ],
            }
        )
    return {
        "ok": True,
        "document_id": source.document_id,
        "revision": source.revision,
        "total_pages": len(pages),
        "offset": offset,
        "pages": records,
        "next_offset": offset + limit if offset + limit < len(pages) else None,
    }


def extraction_evidence(log: dict) -> list[dict]:
    """Previews never become chemical evidence; selection is explicit and auditable."""
    selected = log.get("selected_evidence_ids")
    return [
        e
        for e in log.get("evidence", [])
        if e.get("tool") != "preview_pages" and (selected is None or e.get("id") in selected)
    ]


def pointer(value: Any, path: str) -> Any:
    if not path.startswith("/") or path == "/":
        raise ValueError("field_paths must be non-root JSON pointers, e.g. /optimization_steps/0/reference")
    try:
        for key in path[1:].split("/"):
            key = key.replace("~1", "/").replace("~0", "~")
            value = value[int(key)] if isinstance(value, list) else value[key]
        return copy.deepcopy(value)
    except (KeyError, IndexError, TypeError, ValueError):
        return {"$missing": True}


def import_skill_gaps(log: dict, extraction: dict) -> None:
    """Register Skill questions automatically; disappearance is not proof of resolution."""
    from drugpaper.facts.figure_models import ExtractionGap

    for raw in extraction.get("information_gaps", []):
        report = ExtractionGap.model_validate(raw)
        gap_id = f"skill:{report.gap_id}"
        existing = next((g for g in log.get("gaps", []) if g["gap_id"] == gap_id), None)
        metadata = {
            "source": "skill",
            "category": report.category,
            "next_action": report.next_action,
            "query_terms": report.query_terms,
            "source_hints": report.source_hints,
        }
        # The host ledger owns the question identity and closure, not a fresh LLM output.
        if existing:
            existing.update(metadata)
            if existing["status"] == "resolved":
                existing["status"] = "open"
            continue
        known = {e["id"] for e in log.get("evidence", []) if e.get("tool") != "preview_pages"}
        update_gap(
            log,
            {
                "gap_id": gap_id,
                "question": report.question,
                "field_paths": report.field_paths,
                "evidence_ids": [e for e in report.evidence_ids if e in known],
            },
            extraction,
            False,
            {},
        )
        log["gaps"][-1].update(metadata)


def uninvestigated_skill_gaps(log: dict) -> list[str]:
    """One investigated subject must not authorize abandoning all other subjects."""
    applied = set(log.get("applied_evidence_ids", []))
    pending = []
    for gap in log.get("gaps", []):
        if gap.get("source") != "skill" or gap["status"] == "resolved":
            continue
        disposition = gap["status"] in {"unresolved", "conflict"} and gap.get("note", "").strip()
        supported = bool(gap.get("evidence_ids")) and set(gap["evidence_ids"]) <= applied
        capability_limit = gap.get("category") == "unsupported_representation"
        if not disposition or not (supported or capability_limit):
            pending.append(gap["gap_id"])
    return pending


def update_gap(log: dict, args: dict, extraction: dict, validation_current: bool, validation: dict) -> dict:
    req = GapRequest.model_validate(args)
    if req.status not in {"open", "resolved", "conflict", "unresolved"}:
        raise ValueError("status must be open/resolved/conflict/unresolved")
    gaps = log.setdefault("gaps", [])
    prior = next((g for g in gaps if g["gap_id"] == req.gap_id), None)
    if prior:
        req.question = req.question if req.question is not None else prior["question"]
        req.field_paths = req.field_paths if req.field_paths is not None else prior["field_paths"]
    if not req.question or not req.field_paths:
        raise ValueError(
            "New gaps require question and field_paths; existing gaps need only gap_id and updated status/evidence/note"
        )
    if prior and (prior["question"] != req.question or prior["field_paths"] != req.field_paths):
        raise ValueError(
            "Keep the original question and field_paths; create a new gap for a different question"
        )
    if len(gaps) >= 24 and prior is None:
        raise ValueError("At most 24 gaps per figure")
    values = {p: pointer(extraction, p) for p in req.field_paths}
    by_id = {e["id"]: e for e in log["evidence"]}
    if any(e not in by_id or by_id[e]["tool"] == "preview_pages" for e in req.evidence_ids):
        raise ValueError("Use actual text/high-resolution evidence IDs; previews cannot resolve a gap")
    changes = {
        p: {"before": prior["baseline"][p], "after": values[p]}
        for p in req.field_paths
        if prior and values[p] != prior["baseline"][p]
    }
    if req.status == "resolved":
        if not prior:
            raise GapUpdateRejected(
                "This gap ID is not registered",
                code="gap_not_registered",
                action="Use the existing gap_id from get_workflow_status; register only a genuinely new question.",
                gap_id=req.gap_id,
            )
        if not req.evidence_ids:
            raise GapUpdateRejected(
                "Gap resolution requires the applied evidence IDs",
                code="gap_evidence_required",
                action="Supply actual evidence_ids supporting the changed fields; do not rename the gap.",
                gap_id=req.gap_id,
            )
        if not validation_current:
            raise GapUpdateRejected(
                "The gap is already registered; review current structure if required, then validate_result before closing",
                code="gap_validation_required",
                action="Review the current structure if required, then validate_result before retrying this same gap_id; do not register it again or fetch more sources just to close it.",
                gap_id=req.gap_id,
            )
        applied = set(log.get("applied_evidence_ids", []))
        if prior.get("source") == "skill":
            # Other subjects may remain unclear; this question must have an actual answer.
            if validation.get("status") in {"failed", "not_a_table"}:
                raise ValueError("Extraction validation failed")
            reported = extraction.get("information_gaps", [])
            if any(g.get("gap_id") == req.gap_id.removeprefix("skill:") for g in reported) and not changes:
                raise ValueError(
                    "The latest Skill extraction still reports this gap and affected fields have not changed"
                )
            if not set(req.evidence_ids) <= applied or any(v == {"$missing": True} for v in values.values()):
                raise ValueError("Evidence must be applied and affected fields must exist")
            failures = [
                *validation.get("backfill_errors", []),
                *[
                    c
                    for c in validation.get("checks", [])
                    if c.get("severity") == "error"
                    and not (c.get("code") == "C_BACKFILL_ERR" and validation.get("backfill_errors"))
                ],
            ]
            for failure in failures:
                path = failure.get("field_path", failure.get("path"))
                if not path:
                    raise ValueError("Unlocated validation errors require review before closing the gap")
                try:
                    path = GapRequest.normalize_paths([path])[0]
                except ValueError:
                    raise ValueError(
                        "Unlocated validation errors require review before closing the gap"
                    ) from None
                if any(
                    path == p or path.startswith(p + "/") or p.startswith(path + "/") for p in req.field_paths
                ):
                    raise ValueError("Affected fields still have validation errors")
        else:
            if not set(req.evidence_ids) <= applied or not changes:
                raise ValueError("Evidence must be applied and affected JSON fields must actually change")
            if validation.get("backfill_errors") or any(
                c.get("severity") == "error" for c in validation.get("checks", [])
            ):
                raise ValueError("Resolve validation errors before closing the gap")
            if validation.get("status") in {"unclear", "failed", "not_a_table"}:
                raise ValueError("Extraction is still incomplete")
        if not req.note.strip():
            raise ValueError("Explain briefly which observed evidence answers the question")
    # Closing a validated, evidence-backed question also retires its original
    # report. Requiring a separate collection deletion makes closure impossible
    # after a passing review (which has no issue ID to authorize that deletion).
    if req.status == "resolved" and prior.get("source") == "skill":
        report_id = req.gap_id.removeprefix("skill:")
        retired = [g for g in extraction.get("information_gaps", []) if g.get("gap_id") == report_id]
        if retired:
            from drugpaper.agents.figure.structure_review import fingerprint

            before_hash = fingerprint(extraction)
            extraction["information_gaps"] = [
                g for g in extraction["information_gaps"] if g.get("gap_id") != report_id
            ]
            log.setdefault("gap_resolutions", []).append(
                {
                    "gap_id": req.gap_id,
                    "reports": copy.deepcopy(retired),
                    "evidence_ids": req.evidence_ids,
                    "note": req.note,
                    "extraction_version": log.get("extraction_version", 0),
                }
            )
            # Only bookkeeping changed; preserve a review of identical business
            # fields without spending another independent-review call.
            audit = copy.deepcopy(log.get("structure_audit", {}))
            if audit.get("extraction_hash") == before_hash:
                audit["extraction_hash"] = fingerprint(extraction)
                log["structure_audit"] = audit
    gap = {
        **req.model_dump(),
        "baseline": prior["baseline"] if prior else values,
        "changes": changes,
        "extraction_version": log.get("extraction_version", 0),
    }
    if prior:
        prior.update(gap)
    else:
        gaps.append(gap)
    return {
        "ok": True,
        "gap": gap,
        "note": "Field changes and validation are checked; a resolved Skill report is retired with provenance. Semantic correctness still needs quality review.",
    }


def compact_context(messages: list[Message], max_images: int, max_bytes: int) -> tuple[list[Message], dict]:
    """Remove image payloads only, keeping tool pairs and Responses reasoning items intact."""
    bounded = [m.model_copy(deep=True) for m in messages]
    # Archive bulky old result bodies without deleting protocol pairs or encrypted
    # reasoning items. Raw tool events remain intact; exact fields can be queried again.
    tool_indices = [i for i, m in enumerate(bounded) if m.role == "tool"]
    archived = 0
    for index in tool_indices[:-6]:
        message = bounded[index]
        text = message.text()
        if len(text.encode()) <= 6000:
            continue
        try:
            value = json.loads(text)
        except (ValueError, TypeError):
            continue
        if not isinstance(value, dict):
            continue
        summary = {
            k: value[k]
            for k in (
                "ok",
                "error",
                "repair",
                "evidence_id",
                "extraction_version",
                "status",
                "selected_evidence_ids",
            )
            if k in value
        }
        summary.update(
            archived_tool_result=True,
            tool=message.name,
            available_fields=list(value),
            reload="get_structure for current fields; select_context for saved evidence images; source tools for exact text",
        )
        message.content = json.dumps(summary, ensure_ascii=False)
        archived += 1
    pinned = [
        (i, j)
        for i, m in enumerate(bounded)
        if i < 2 and isinstance(m.content, list)
        for j, p in enumerate(m.content)
        if isinstance(p, ImagePart)
    ]
    optional = [
        (i, j)
        for i, m in enumerate(bounded)
        if i >= 2 and isinstance(m.content, list)
        for j, p in enumerate(m.content)
        if isinstance(p, ImagePart)
    ]
    if len(pinned) > max_images:
        raise ContextBudgetError(
            "Target segments exceed context image budget; increase budget or process segments separately"
        )
    removed = 0

    def size():
        return len(ChatRequest(messages=bounded).model_dump_json().encode())

    total = len(pinned) + len(optional)
    for i, j in optional:
        if total <= max_images and size() <= max_bytes:
            break
        bounded[i].content[j] = TextPart(
            text="[Image payload released; evidence remains stored. Use select_context to reload.]"
        )
        total -= 1
        removed += 1
    if size() > max_bytes:
        raise ContextBudgetError(
            "Context request byte budget exceeded; target/text history cannot be safely compacted"
        )
    return bounded, {
        "active_images": total,
        "released_images": removed,
        "archived_tool_results": archived,
        "request_bytes": size(),
    }
