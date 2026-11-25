from __future__ import annotations

import re
from typing import Any

from rdkit import Chem


def parse_hrms(text: str) -> dict[str, str] | None:
    match = re.search(
        r"(?i:calcd\.?|calculated)(?i:\s+mass)?\s+for\s+((?:[A-Z][a-z]?\d*)+)\s*(\[[^\]]+\][+−-]?)?",
        text,
    )
    if not match:
        return None
    return {"formula": match[1], "adduct": match[2] or ""}


def composition(formula: str) -> dict[str, int]:
    """Flat elemental formulas only; unsupported isotope/salt notation stays unverified."""
    tokens = list(re.finditer(r"([A-Z][a-z]?)(\d*)", formula))
    if not tokens or "".join(m[0] for m in tokens) != formula:
        raise ValueError("unsupported_formula")
    counts: dict[str, int] = {}
    table = Chem.GetPeriodicTable()
    for token in tokens:
        element, count = token[1], int(token[2] or 1)
        try:
            atomic_number = table.GetAtomicNumber(element)
        except RuntimeError as exc:
            raise ValueError("unknown_element") from exc
        if atomic_number <= 0 or not 0 < count <= 10000:
            raise ValueError("invalid_composition")
        counts[element] = counts.get(element, 0) + count
    return counts


def check_hrms(formula: str | None, reported: dict[str, str] | None) -> dict[str, Any]:
    """Accept papers reporting either neutral M or the explicit adduct composition.

    The interpretation is recorded; a composition match is not an exact-mass check.
    """
    result: dict[str, Any] = {"formula_match": None, "hrms": reported, "formula_basis": None}
    if formula is None or reported is None:
        return result
    try:
        expected, actual = composition(formula), composition(reported["formula"])
    except ValueError:
        return {**result, "formula_error": "unsupported_formula"}
    adduct = re.sub(r"\s+", "", reported.get("adduct", "")).replace("−", "-")
    additions: dict[str, dict[str, int]] = {
        "": {},
        "[M]+": {},
        "[M]-": {},
        "[M+H]+": {"H": 1},
        "[M-H]-": {"H": -1},
        "[M+Na]+": {"Na": 1},
        "[M+K]+": {"K": 1},
        "[M+NH4]+": {"N": 1, "H": 4},
    }
    if adduct not in additions:
        return {**result, "formula_error": "unsupported_adduct"}
    if actual == expected:
        return {**result, "formula_match": True, "formula_basis": "neutral"}
    ion = dict(expected)
    for element, delta in additions[adduct].items():
        ion[element] = ion.get(element, 0) + delta
    ion = {element: count for element, count in ion.items() if count}
    matched = actual == ion and all(count > 0 for count in ion.values())
    return {**result, "formula_match": matched, "formula_basis": "adduct" if matched else "mismatch"}
