"""编号、别名、候选优先级与结构冲突；不依赖模型。"""

from __future__ import annotations

import re
from typing import Any

from drugpaper.domain.ids import new_id
from drugpaper.facts.labels import expand_range, normalize


def priority(candidate: dict[str, Any]) -> int:
    if not candidate.get("validation", {}).get("valid") or candidate.get("is_markush"):
        return -1
    source = candidate["source"]
    if source == "manual":
        return 100
    if source == "opsin":
        validation = candidate.get("validation", {})
        if validation.get("opsin_status") == "warning":
            return -1
        match = validation.get("formula_match")
        return 90 if match is True else 30 if match is False else 70
    if source == "ocsr":
        match = candidate.get("validation", {}).get("formula_match")
        return (
            80
            if match is True
            or match is not False
            and candidate.get("confidence") is not None
            and candidate["confidence"] >= 0.8
            else 40
        )
    return 60 if source == "markush_assembly" else 10


def resolve(
    document_id: str,
    *,
    claims: list[dict[str, Any]],
    structures: list[dict[str, Any]],
    text: str = "",
    existing: list[dict[str, Any]] | None = None,
    assignments: list[dict[str, Any]] | None = None,
    scope: list[str] | None = None,
    mentions: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    aliases: dict[str, str] = {}
    for compound in existing or []:
        for alias in compound.get("aliases", []):
            aliases[normalize(alias)] = compound["primary_label"]
    for match in re.finditer(r"\b(\d+[a-z]?)\s*[\[(]\s*([A-Z]{2,}-\d+)", text):
        aliases[normalize(match[2])] = normalize(match[1])
    # A contradictory alias must not be merged by whichever claim happens to be last.
    alias_parents: dict[str, set[str]] = {alias: {parent} for alias, parent in aliases.items()}
    for mention in mentions or []:
        for alias in mention.get("aliases", []):
            alias_parents.setdefault(normalize(alias), set()).add(normalize(mention["label"]))
    ambiguous_aliases = {alias: parents for alias, parents in alias_parents.items() if len(parents) > 1}
    aliases = {alias: next(iter(parents)) for alias, parents in alias_parents.items() if len(parents) == 1}
    mentions = mentions or []
    known = {c["primary_label"]: c for c in existing or []}
    labels = {normalize(c["label"]) for c in claims} | set(aliases.values())
    labels.update(known)
    labels.update(normalize(m["label"]) for m in mentions)
    labels |= {normalize(a["label"]) for a in assignments or []}
    for match in re.finditer(r"\bcompounds?\s+(\([RS]\)-\d+|\d+[a-z]?(?:[−–-]\d+)?)", text, flags=re.I):
        labels.update(expand_range(match[1]))
    labels = {aliases.get(label, label) for label in labels if label}
    for label in list(labels):
        if re.fullmatch(r"\([RS]\)-\d+[a-z]?", label):
            labels.add(label[4:])
    compounds, conflicts, bindings = [], [], {}
    by_id = {s["structure_id"]: s for s in structures}
    by_node = {s["node_ref"]: s for s in structures if s.get("node_ref")}
    claims = [
        {**claim, "structure_id": by_node[claim["node_ref"]]["structure_id"]}
        if not claim.get("structure_id") and claim.get("node_ref") in by_node
        else claim
        for claim in claims
    ]
    for label in sorted(labels):
        old = known.get(label, {})
        if scope is not None and old.get("compound_id") not in scope:
            continue
        compound_id = old.get("compound_id") or new_id("cmp")
        candidates = [
            by_id[c["structure_id"]]
            for c in claims
            if aliases.get(normalize(c["label"]), normalize(c["label"])) == label
            and c.get("structure_id") in by_id
        ]
        candidates += [s for s in structures if s.get("compound_id") == compound_id]
        ids = {s["structure_id"] for s in candidates}
        candidates = [by_id[i] for i in sorted(ids)]
        valid = sorted(
            (s for s in candidates if priority(s) >= 0), key=lambda s: (-priority(s), s["structure_id"])
        )
        accepted = valid[0]["structure_id"] if valid else None
        if "accepted_structure_id" in old.get("locked_fields", []):
            accepted = old.get("accepted_structure_id")
        stereo_parent = (
            known.get(label[4:], {}).get("compound_id") if label.startswith(("(S)-", "(R)-")) else None
        )
        compound = {
            "compound_id": compound_id,
            "document_id": document_id,
            "primary_label": label,
            "aliases": sorted(
                set(old.get("aliases", []))
                if "aliases" in old.get("locked_fields", [])
                else (set(old.get("aliases", [])) - set(ambiguous_aliases))
                | {alias for alias, parent in aliases.items() if parent == label}
            ),
            "role": "unknown"
            if old.get("role_status") == "needs_review" and "role" not in old.get("locked_fields", [])
            else old.get("role", "unknown"),
            "accepted_structure_id": accepted,
            "stereo_parent_id": stereo_parent,
            "status": "active",
            "locked_fields": old.get("locked_fields", []),
            "evidence": (old.get("evidence", []) if old.get("role_status") == "confirmed" else [])
            + [e for m in mentions if normalize(m["label"]) == label for e in m.get("evidence", [])]
            + [
                e
                for claim in claims
                if aliases.get(normalize(claim["label"]), normalize(claim["label"])) == label
                for e in claim.get("evidence", [])
            ],
            "role_evidence": old.get("role_evidence", [])
            if old.get("role_status") == "confirmed" or "role" in old.get("locked_fields", [])
            else [],
            "role_status": old.get("role_status", "unknown")
            if old.get("role_status") == "confirmed" or "role" in old.get("locked_fields", [])
            else "unknown",
        }
        compound["evidence"] = list({str(e): e for e in compound["evidence"]}.values())
        relevant = [m for m in mentions if normalize(m["label"]) == label]
        if "role" not in compound["locked_fields"] and compound["role_status"] != "confirmed":
            roles = {m.get("role") for m in relevant} & {"hit", "lead", "final", "analog", "reference"}
            repeated = {m.get("section_role") for m in relevant} >= {"abstract", "conclusion"}
            if len(roles) == 1 or not roles and repeated:
                compound["role"] = next(iter(roles)) if roles else "final"
                compound["role_status"] = "needs_review"
                compound["role_evidence"] = [e for m in relevant for e in m.get("evidence", [])]
        compounds.append(compound)
        for candidate in candidates:
            bindings[candidate["structure_id"]] = compound_id
        keys = {s["inchikey"] for s in valid if s.get("inchikey")}
        if len(keys) > 1:
            conflicts.append(
                {
                    "compound_id": compound_id,
                    "type": "connectivity" if len({key[:14] for key in keys}) > 1 else "stereochemistry",
                    "structure_ids": sorted(ids),
                    "evidence": [e for s in valid for e in s["evidence"]],
                }
            )
        for alias, parents in ambiguous_aliases.items():
            if label in parents:
                evidence = [
                    e for m in mentions if alias in m.get("aliases", []) for e in m.get("evidence", [])
                ]
                if evidence:
                    conflicts.append(
                        {
                            "compound_id": compound_id,
                            "type": "alias",
                            "alias": alias,
                            "labels": sorted(parents),
                            "structure_ids": [],
                            "evidence": evidence,
                        }
                    )
        mismatched = [s for s in candidates if s.get("validation", {}).get("formula_match") is False]
        if mismatched:
            conflicts.append(
                {
                    "compound_id": compound_id,
                    "type": "formula",
                    "structure_ids": [s["structure_id"] for s in mismatched],
                    "evidence": [e for s in mismatched for e in s["evidence"]],
                }
            )
        if (
            "accepted_structure_id" in old.get("locked_fields", [])
            and valid
            and accepted != valid[0]["structure_id"]
        ):
            conflicts.append(
                {
                    "compound_id": compound_id,
                    "type": "locked_structure",
                    "structure_ids": [accepted, valid[0]["structure_id"]],
                    "evidence": valid[0]["evidence"],
                }
            )
    compound_parents = {
        **{k: v["compound_id"] for k, v in known.items()},
        **{c["primary_label"]: c["compound_id"] for c in compounds},
    }
    for compound in compounds:
        if compound["primary_label"].startswith(("(S)-", "(R)-")):
            compound["stereo_parent_id"] = compound_parents.get(compound["primary_label"][4:])
    return {"compounds": compounds, "conflicts": conflicts, "bindings": bindings}
