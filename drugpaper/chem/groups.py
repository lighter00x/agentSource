"""Shared, closed chemical abbreviation library, independent of figure labels."""

from drugpaper.chem.splicer.vendor import get_abbrev_smi

# Common terminal functional groups supplement the bundled abbreviation library.
EXTRA_GROUPS = {
    "CHO": "C=O",
    "COMe": "C(=O)C",
    "COOMe": "C(=O)OC",
    "COOEt": "C(=O)OCC",
    "SO2NH2": "S(=O)(=O)N",
    "SO2NHMe": "S(=O)(=O)NC",
    "SO2Me": "S(=O)(=O)C",
    "COOH": "C(=O)O",
    "CONH2": "C(=O)N",
    "SOMe": "S(=O)C",
    "SOEt": "S(=O)CC",
    "SO(NH)Et": "S(=O)(=N)CC",
    "SO(NH)Me": "S(=O)(=N)C",
    "SO(NMe)Me": "S(=O)(=NC)C",
    "OCH3": "OC",
    "OCH2F": "OCF",
    "OCHF2": "OC(F)F",
    "SCH3": "SC",
    "SCH2F": "SCF",
    "SCHF2": "SC(F)F",
}
ALIASES = {"OCH(H)F": "OCH2F", "OCH(F)F": "OCHF2", "SCH(H)F": "SCH2F", "SCH(F)F": "SCHF2"}


def abbreviations() -> dict[str, str]:
    return {**get_abbrev_smi(), **EXTRA_GROUPS, **{e: e for e in ("F", "Cl", "Br", "I")}}


def normalize_group(value: str) -> str:
    # Only closed equivalences; positions and open classes are never discarded.
    return ALIASES.get(value, value)
