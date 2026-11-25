"""Evidence navigation and per-question stopping guards within the owned document."""

from __future__ import annotations

import re

from drugpaper.agents.figure.context import GapRequest, pointer
from drugpaper.agents.figure.reference_assignments import find_reference_visuals, visual_navigation_text
from drugpaper.facts.compound_series import numeric_members

CITATION = re.compile(r"\b(Fig(?:ure)?\.?|Scheme|Table)\s*(S?\d+[A-Za-z]?)\b", re.I)


def citations(text: str) -> set[tuple[str, str]]:
    return {
        ("figure" if kind.lower().startswith("fig") else kind.lower(), number.lower())
        for kind, number in CITATION.findall(text)
    }


def texts(value):
    """Read returned text only; request reasons/queries do not establish source references."""
    if isinstance(value, dict):
        for key, item in value.items():
            if key in {"text", "text_raw", "text_norm", "snippet", "caption"} and isinstance(item, str):
                yield item
            elif isinstance(item, (dict, list)):
                yield from texts(item)
    elif isinstance(value, list):
        for item in value:
            yield from texts(item)


def viewed(node: dict, evidence: list[dict]) -> bool:
    for item in evidence:
        result = item.get("result", {})
        if item.get("tool") == "inspect_visual" and result.get("node_ref") == node["node_id"]:
            return True
        # A custom crop or a thumbnail does not establish that the source was read.
        if (
            item.get("tool") == "inspect_page"
            and item.get("arguments", {}).get("bbox") is None
            and node["pages"]
            and set(node["pages"]) <= {r.get("page") for r in result.get("image_refs", [])}
        ):
            return True
    return False


def questions(state: dict, validation: dict) -> list[dict]:
    result = [dict(g) for g in state["exploration"].get("gaps", []) if g["status"] != "resolved"]
    seen = set()
    for error in validation.get("backfill_errors", []):
        reason = str(error.get("reason", ""))
        if not any(word in reason for word in ("definition", "missing", "unresolved", "abbreviation")):
            continue
        try:
            path = GapRequest.normalize_paths([error["field_path"]])[0]
        except (KeyError, ValueError):
            continue
        identity = (path, reason)
        if identity in seen:
            continue
        seen.add(identity)
        result.append(
            {
                "gap_id": f"assembly-evidence:{len(seen)}",
                "status": "open",
                "question": "Find the paper's explicit substituent/member definition before declaring a tool limitation: "
                + reason,
                "field_paths": [path],
                "query_terms": [reason],
            }
        )
    return result


def investigation_plan(source, state: dict, validation: dict) -> dict:
    evidence = state["exploration"].get("evidence", [])
    plans = []
    for gap in questions(state, validation):
        members = set()
        for path in gap["field_paths"]:
            while path:
                value = pointer(state.get("extraction", {}), path)
                if isinstance(value, dict) and value.get("compound_series"):
                    members.update(value["compound_series"].get("members", []))
                    break
                path = path.rsplit("/", 1)[0]
        if not members:
            for label in re.findall(r"\d+\s*[-–−—]\s*\d+|\d+[a-z]?", gap["question"]):
                members.update(numeric_members(label) or [label])
        terms = [t for t in gap.get("query_terms", []) if len(t) <= 100][:12]
        candidates = find_reference_visuals(
            source,
            state["node_ref"],
            {
                "references": sorted(members)[:32] or terms[:1] or [gap["question"][:100]],
                "terms": terms,
                "limit": 12,
            },
        )["candidates"]
        cited = set()
        for item in evidence:
            for text in texts(item.get("result", {})):
                # Citations from a paragraph about the affected members are actionable leads.
                prose = CITATION.sub("", text)
                if members and not any(
                    re.search(r"(?<!\w)" + re.escape(m) + r"(?!\w)", prose) for m in members
                ):
                    continue
                cited.update(citations(text))
        by_ref = {c["node_id"]: c for c in candidates}
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
            if ref == state["node_ref"]:
                continue
            matched = (
                citations(str(node.label or "") + " " + visual_navigation_text(source, node)[:240]) & cited
            )
            if not matched:
                continue
            candidate = by_ref.setdefault(
                ref,
                {
                    "node_id": ref,
                    "label": node.label,
                    "type": node.node_type,
                    "pages": [r.page for r in node.geometry.regions],
                    "score": 0,
                    "caption_preview": source.text_of(node)[:160],
                },
            )
            candidate["score"] += 6
            candidate["cited_in_read_text"] = True
        ranked = sorted(by_ref.values(), key=lambda c: (-c["score"], c["node_id"]))[:3]
        for candidate in ranked:
            candidate["viewed"] = viewed(candidate, evidence)
            candidate["caption_preview"] = candidate.get("caption_preview", "")[:160]
        # A single tangential read must not stand in for following multiple concrete leads.
        minimum = min(2, len(ranked))
        remaining = max(0, minimum - sum(c["viewed"] for c in ranked))
        complete_leads = [c for c in ranked if c.get("reference_coverage") == 1 and not c["viewed"]]
        if complete_leads:
            remaining = max(remaining, len(complete_leads))
        plans.append(
            {
                "gap_id": gap["gap_id"],
                "question": gap["question"],
                "field_paths": gap["field_paths"],
                "candidates": ranked,
                "additional_source_reads_required": remaining,
                "unread_complete_member_sources": [c["node_id"] for c in complete_leads],
                "next_action": "inspect_visual"
                if remaining
                else "apply_partial_evidence_or_document_remaining_gap",
            }
        )
    return {
        "ok": True,
        "questions": plans,
        "rule": "Candidates are navigation, not facts. Inspect relevant source nodes; follow their citations and search missing terms. Apply each confirmed member locally even if the rest remain unknown. An open abbreviation is missing evidence until source definitions have been checked, not proof of unsupported chemistry.",
    }


def require_investigation(source, state: dict, validation: dict) -> None:
    plan = investigation_plan(source, state, validation)
    pending = [q for q in plan["questions"] if q["additional_source_reads_required"]]
    state["exploration"]["investigation_plan"] = plan
    if pending:
        import json

        raise ValueError(
            "Cannot finish with unexamined document evidence leads. Inspect relevant source images, "
            "record confirmed partial assignments, and validate. Use get_investigation_plan: "
            + json.dumps(pending, ensure_ascii=False)
        )
