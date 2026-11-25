"""Lossless source-to-computation projection for explicit attachment assignments.

This does not modify evidence or infer new substituents. Only exact symbolic
substitution and closed positional aliases are accepted; contradictions fail.
"""

from __future__ import annotations

import itertools
import re

from drugpaper.chem.assembly import normalize_group
from drugpaper.chem.formula import composition


def local_group(expression: str, assignment: dict[str, str]) -> str:
    if not assignment:
        return expression
    pattern = re.compile("|".join(re.escape(k) for k in sorted(assignment, key=len, reverse=True)))
    result = pattern.sub(lambda m: assignment[m[0]], expression)
    if re.fullmatch(r"(?:O|S|N)?C(?:H|F|Cl|Br|I|[0-9])+", result):
        counts = composition(result)
        order = list(dict.fromkeys(re.findall(r"[A-Z][a-z]?", result)))
        order = [e for e in order if e not in {"H", "F", "Cl", "Br", "I"}] + [
            e for e in ("H", "F", "Cl", "Br", "I") if e in counts
        ]
        return "".join(e + (str(counts[e]) if counts[e] != 1 else "") for e in order)
    return result


def project_inputs(
    labels: list[str], groups: dict, positions: dict | None = None, attachments: list[str] | None = None
) -> dict:
    if attachments is not None:
        if (
            not attachments
            or len(set(attachments)) != len(attachments)
            or any(not isinstance(k, str) or k not in groups or k not in labels for k in attachments)
        ):
            raise ValueError("invalid_attachment_selection")
        if set(attachments) != set(labels):
            raise ValueError("incomplete_attachment_selection")
        selected = {k: groups[k] for k in attachments}
        result = project_inputs(labels, selected, positions)
        result.update(
            source_substituents=dict(groups),
            attachments=list(attachments),
            selection_mode="explicit_attachments",
            explanatory_substituents={k: v for k, v in groups.items() if k not in attachments},
        )
        return result
    original_groups = dict(groups)
    output = {k: normalize_group(v) if isinstance(v, str) else v for k, v in groups.items()}
    definitions = dict(positions or {})
    aliases = {}
    consumed = set()
    for label in labels:
        variables = {
            key: value
            for key, value in original_groups.items()
            if key not in labels
            and re.fullmatch(r"[A-Z][a-z]?\d*", key)
            and key in label
            and isinstance(value, str)
        }
        if not variables:
            continue
        if len(variables) > 8:
            raise ValueError("composite_assignment_projection_limit")
        # A partially substituted name is an alias only if the retained source
        # variables prove its exact relation to the real attachment label.
        keys = list(variables)
        names = {label}
        for mask in itertools.product((False, True), repeat=len(keys)):
            selection = {k: variables[k] for k, include in zip(keys, mask, strict=True) if include}
            names.add(local_group(label, selection))
        present = [name for name in names if name in original_groups and name not in variables]
        if not present:
            continue
        expected = normalize_group(local_group(label, variables))
        if any(
            not isinstance(original_groups[name], str) or normalize_group(original_groups[name]) != expected
            for name in present
        ):
            raise ValueError("composite_assignment_conflict")
        output[label] = expected
        consumed.update(variables)
        for name in present:
            if name != label:
                if name in labels:
                    raise ValueError("ambiguous_attachment_alias")
                aliases[name] = label
                output.pop(name, None)
    for name in consumed:
        output.pop(name, None)

    from drugpaper.chem.assembly_compat import normalize_legacy_positions

    definitions = normalize_legacy_positions(labels, output, definitions, aliases)
    # A closed locant already embedded in the selected group is one constraint,
    # not a second positional adapter request. Keep original evidence below.
    for key, value in list(definitions.items()):
        prime_map = str.maketrans({"′": "'", "’": "'"})
        position = value.translate(prime_map).strip()
        group = output.get(key)
        match = re.fullmatch(r"[2-6]'?-(F|Cl|Br|I)", position)
        if (
            key in labels
            and isinstance(group, str)
            and match
            and group.translate(prime_map).strip() in {position, match[1]}
        ):
            output[key] = position
            definitions.pop(key)
    return {
        "substituents": output,
        "position_definitions": definitions,
        "source_substituents": original_groups,
        "source_position_definitions": dict(positions or {}),
        "attachment_aliases": aliases,
        "composite_variables": {k: original_groups[k] for k in sorted(consumed)},
        "selection_mode": "legacy_projection",
    }


def preserves_assignment(labels: list[str], required: dict, observed: dict) -> bool:
    if required.get("attachments") is not None and set(required["attachments"]) != set(
        observed.get("attachments") or []
    ):
        return False
    try:
        before = project_inputs(
            labels, required.get("substituents", {}), required.get("position_definitions", {})
        )
        after = project_inputs(
            labels, observed.get("substituents", {}), observed.get("position_definitions", {})
        )
        # Validate explicit selectors separately from equivalence of retained
        # source facts; excluding a field from assembly must not erase evidence.
        for value in (required, observed):
            if value.get("attachments") is not None:
                project_inputs(
                    labels,
                    value.get("substituents", {}),
                    value.get("position_definitions", {}),
                    value["attachments"],
                )
    except ValueError:
        return False
    return all(
        all(key in after[field] and value == after[field][key] for key, value in before[field].items())
        for field in ("substituents", "position_definitions")
    )
