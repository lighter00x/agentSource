"""Check bounded local-expression consistency without interpreting recognition structures.

The agent still interprets and normalizes paper notation. These checks only
reject provable contradictions in flat, explicitly assigned local expressions;
unsupported notation remains for source review, never an inferred molecule.
"""

from __future__ import annotations

import re


def _counts(text: str) -> dict[str, int] | None:
    tokens = list(re.finditer(r"([A-Z][a-z]?)([0-9]*)", text))
    if not tokens or "".join(t[0] for t in tokens) != text:
        return None
    counts: dict[str, int] = {}
    for token in tokens:
        element, count = token[1], int(token[2] or 1)
        if (
            element not in {"C", "H", "O", "N", "S", "P", "B", "Si", "F", "Cl", "Br", "I"}
            or not 0 < count <= 100
        ):
            return None
        counts[element] = counts.get(element, 0) + count
    return counts


def normalization_checks(product: dict, assignments: list[dict]) -> list[dict]:
    markush = product.get("markush") or {}
    variables = markush.get("variable_options") or {}
    if not variables:
        return []
    pattern = re.compile("|".join(re.escape(k) for k in sorted(variables, key=len, reverse=True)))
    expressions = dict.fromkeys(c["expression"] for c in markush.get("substitution_cases", []))
    checks = []
    for index, member in enumerate(assignments):
        groups = member.get("substituents", {})
        for expression in expressions:
            keys = list(dict.fromkeys(pattern.findall(expression)))
            if not keys or expression in variables:
                continue
            check = {
                "assignment_index": index,
                "reference": member.get("reference"),
                "expression": expression,
                "original_assignments": {k: groups[k] for k in keys if k in groups},
                "observed_group": groups.get(expression),
                "status": "not_checked",
                "scope": "local_explicit_atom_counts_only_not_connectivity_or_assembly",
            }
            checks.append(check)
            if not all(isinstance(groups.get(k), str) for k in keys):
                check["reason"] = "internal_assignments_not_available"
                continue
            # Only flat atomic values and literal atoms are in this checker.
            # Abbreviations, branches, repeated groups, and open classes remain
            # LLM/source-review tasks rather than expanding this into a parser.
            if any(_counts(groups[k]) is None for k in keys):
                check["reason"] = "unsupported_local_notation"
                continue
            substituted = pattern.sub(lambda m, values=groups: values[m[0]], expression)
            expected = _counts(substituted)
            if expected is None:
                check["reason"] = "unsupported_local_notation"
                continue
            check.update(substituted_expression=substituted, expected_explicit_counts=expected)
            if expression not in groups:
                check.update(status="inconsistent", reason="missing_complete_local_group")
                continue
            observed = _counts(str(groups[expression]))
            if observed is None:
                check["reason"] = "unsupported_normalized_notation"
                continue
            check.update(observed_explicit_counts=observed)
            if expected != observed:
                check.update(status="inconsistent", reason="explicit_atom_count_mismatch")
            elif member.get("attachments") is not None and (
                expression not in member["attachments"] or set(keys) & set(member["attachments"])
            ):
                check.update(status="inconsistent", reason="internal_variables_selected_as_attachments")
            else:
                check["status"] = "consistent"
    return checks


def member_normalization_gaps(extraction: dict) -> list[dict]:
    issues = []
    for pi, pathway in enumerate(extraction.get("pathways", [])):
        for si, step in enumerate(pathway.get("steps", [])):
            for mi, product in enumerate(step.get("products", [])):
                for check in normalization_checks(product, product.get("variants", [])):
                    if check["status"] != "inconsistent":
                        continue
                    path = f"/pathways/{pi}/steps/{si}/products/{mi}/variants/{check['assignment_index']}"
                    issues.append(
                        {
                            "issue_id": f"member-normalization-{len(issues) + 1}",
                            "field_path": path,
                            "category": "other",
                            "severity": "error",
                            "description": f"Member local normalization contradicts its image-derived expression: {check['reason']}.",
                            "visible_evidence": str(check),
                            "suggested_action": "Recheck the source expression and member assignments; locally correct the complete group and select that group instead of its internal variables. Preserve positions/evidence. Correct registered source records with supersedes and correction_reason; do not bypass them through assembly trials.",
                        }
                    )
    return issues
