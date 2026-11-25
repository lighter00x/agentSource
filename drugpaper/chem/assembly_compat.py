"""Bounded positional adapters. Generic atom attachment never depends on these conventions."""

from __future__ import annotations

import re

from rdkit import Chem

from drugpaper.chem.splicer.adapter import substitute

POSITION_KEYS = {"CHO position", "XCHYZ position", "X", "Y, Z"}


def capabilities() -> list[dict]:
    return [
        {
            "name": "explicit_atom_attachment",
            "scope": "Any recognized scaffold with uniquely labelled terminal single-bond sites; closed abbreviations or owned fragments; labels are opaque names.",
        },
        {
            "name": "aromatic_atom_replacement",
            "scope": "Labelled aromatic sites: CH/N/NH/O/S, subject to valence and aromaticity validation.",
        },
        {
            "name": "biaryl_ring_locants",
            "scope": "Two identifiable six-membered rings; arbitrary site labels, one terminal group, one CH/N aromatic site, and an adjacent unique fixed substituent establishing primed ring direction. Explicit F/Cl/Br/I locants only; ambiguous numbering fails.",
        },
        {
            "name": "legacy_biaryl_aldehyde_positions",
            "scope": "Compatibility only: verified biaryl aldehyde and legacy table position fields. Other position systems require a matching adapter; never rename evidence to force a match.",
        },
    ]


def positional_assignment(caption: str, groups: dict[str, str], positions: dict[str, str]) -> tuple[str, str]:
    """Retain proven historical position semantics without making them the default contract."""
    from drugpaper.chem.assembly import _remove_placeholder

    if set(positions) != POSITION_KEYS or set(groups) != {"XCHYZ"}:
        raise ValueError("unsupported_positional_definition")
    yz = [v.strip() for v in positions["Y, Z"].split(",")]
    if positions["X"] not in {"O", "S"} or yz not in [["H", "F"], ["F", "F"]]:
        raise ValueError("unsupported_positional_assignment")
    group = positions["X"] + ("CH2F" if yz == ["H", "F"] else "CHF2")
    if groups["XCHYZ"] != group:
        raise ValueError("positional_group_assignment_conflict")
    prepared = _remove_placeholder(caption, "XCHYZ")
    result = substitute(prepared, positions, max_outputs=1)
    if result.status != "completed" or len(result.products) != 1:
        raise ValueError(result.error_code or "ambiguous_assembly")
    return result.products[0], "explicit_positional_assignment"


def ring_position_assignment(caption: str, groups: dict[str, str]) -> tuple[str, str] | None:
    """Infer computational roles from graph topology, never from R/X/Y label spelling."""
    from drugpaper.chem.assembly import _remove_placeholder

    positional = [k for k, v in groups.items() if re.fullmatch(r"[2-6]['′’]?-(F|Cl|Br|I)", v)]
    if len(positional) != 1 or len(groups) != 3:
        return None
    position_label = positional[0]
    core = caption.split("<sep>", 1)[0]
    mol = Chem.MolFromSmiles(core)
    if mol is None:
        raise ValueError("invalid_scaffold")
    tags = re.findall(r"<a>(\d+):([^<]+)</a>", caption)
    if len(tags) != 3 or {label for _, label in tags} != set(groups):
        return None
    roles = {position_label: "X"}
    for raw_index, label in tags:
        if label == position_label:
            continue
        index = int(raw_index)
        if index >= mol.GetNumAtoms():
            raise ValueError("template_annotation_out_of_bounds")
        atom = mol.GetAtomWithIdx(index)
        role = (
            "Y" if atom.GetIsAromatic() and atom.GetDegree() == 2 else "R" if atom.GetDegree() == 1 else None
        )
        if role is None or role in roles.values():
            return None
        roles[label] = role
    prepared = _remove_placeholder(caption, position_label)
    # Canonical names are private adapter roles; source labels/assignments stay unchanged.
    prepared = re.sub(r"<a>(\d+):([^<]+)</a>", lambda m: f"<a>{m[1]}:{roles[m[2]]}</a>", prepared)
    result = substitute(prepared, {roles[k]: v for k, v in groups.items()}, max_outputs=1)
    if result.status != "completed" or len(result.products) != 1:
        raise ValueError(result.error_code or "ambiguous_assembly")
    return result.products[0], "explicit_labeled_ring_position"


def normalize_legacy_positions(labels: list[str], output: dict, definitions: dict, aliases: dict) -> dict:
    definitions = {key: normalize_locant_text(value) for key, value in definitions.items()}
    # These aliases belong only to the existing verified biaryl-aldehyde
    # position adapter. Unknown position keys are retained, never discarded.
    if labels == ["XCHYZ"] and definitions:
        key_aliases = {"CHO": "CHO position", "XCHYZ": "XCHYZ position"}
        key_aliases.update(
            {alias: "XCHYZ position" for alias, target in aliases.items() if target == "XCHYZ"}
        )
        normalized = {}
        for key, value in definitions.items():
            name = key_aliases.get(key, key)
            value = value.translate(str.maketrans({"′": "'", "’": "'"})).strip()
            if name in normalized and normalized[name] != value:
                raise ValueError("position_alias_conflict")
            normalized[name] = value
        # A closed, unambiguous local group supplies adapter metadata; it is not
        # permission to infer either ring position or an undefined Alk group.
        group = output.get("XCHYZ")
        match = re.fullmatch(r"([OS])(CH2F|CHF2)", group or "")
        if match:
            known = {"X": match[1], "Y, Z": "H, F" if match[2] == "CH2F" else "F, F"}
            for key, value in known.items():
                if key in normalized:
                    observed = normalized[key]
                    if key == "Y, Z":
                        observed = ", ".join(sorted((v.strip() for v in observed.split(",")), reverse=True))
                    if observed != value:
                        raise ValueError("positional_group_assignment_conflict")
                normalized[key] = value
        definitions = normalized
    return definitions


def normalize_locant_text(value: str) -> str:
    """Strip closed descriptive wrappers, never select a number from free prose."""
    normalized = value.translate(str.maketrans({"′": "'", "’": "'"})).strip()
    if re.fullmatch(r"[1-9][0-9]*'?", normalized):
        return normalized
    match = re.fullmatch(
        r"(?P<prefix>(?:(?:upper|lower|top|bottom|left|right|prime|primed|unprimed|ring)\b\s*|[上下左右]方?|苯环|芳环|环|的|prime)*)"
        r"(?P<locant>[1-9][0-9]*'?)\s*(?:位|位点|位号|position|pos\.?)?",
        normalized,
        re.IGNORECASE,
    )
    if match:
        locant = match["locant"]
        # A prime must be explicit, either as a glyph or a word qualifier.
        if not locant.endswith("'") and re.search(r"\bprimed\b|prime(?=环)", match["prefix"], re.I):
            locant += "'"
        return locant
    return normalized
