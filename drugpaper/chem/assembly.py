"""Shared bounded assembly routing; callers supply trusted recognition, never LLM structures."""

from __future__ import annotations

import json
import re

from rdkit import Chem

from drugpaper.chem.esmiles import split
from drugpaper.chem.groups import ALIASES as ALIASES
from drugpaper.chem.groups import normalize_group as normalize_group


def _remove_placeholder(caption: str, label: str) -> str:
    """Remove only an explicitly named terminal dummy; preserve every real atom and other tag."""
    core, extension = split(caption)
    if re.search(r"<[rcds]>", extension):
        raise ValueError("positional_annotations_require_review")
    tags = re.findall(r"<a>(\d+):([^<]+)</a>", extension)
    target = [int(i) for i, name in tags if name == label]
    if len(target) != 1:
        raise ValueError("missing_or_ambiguous_positional_placeholder")
    mol = Chem.MolFromSmiles(core)
    if mol is None:
        raise ValueError("invalid_scaffold")
    index = target[0]
    if index >= mol.GetNumAtoms():
        raise ValueError("positional_placeholder_out_of_bounds")
    atom = mol.GetAtomWithIdx(index)
    if (
        atom.GetAtomicNum() != 0
        or atom.GetDegree() != 1
        or atom.GetBonds()[0].GetBondType() != Chem.BondType.SINGLE
    ):
        raise ValueError("unsupported_positional_placeholder")
    rw = Chem.RWMol(mol)
    rw.RemoveAtom(index)
    prepared = rw.GetMol()
    Chem.SanitizeMol(prepared)
    new_core = Chem.MolToSmiles(prepared, canonical=False)
    output_order = json.loads(prepared.GetProp("_smilesAtomOutputOrder"))
    order = {old: new for new, old in enumerate(output_order)}

    def remap(match: re.Match) -> str:
        old, name = int(match[1]), match[2]
        if old == index:
            return ""
        return f"<a>{order[old - (old > index)]}:{name}</a>"

    new_extension = re.sub(r"<a>(\d+):([^<]+)</a>", remap, extension)
    return new_core + ("<sep>" + new_extension if new_extension else "")


def assemble_explicit(
    caption: str, groups: dict[str, str], positions: dict[str, str] | None = None
) -> tuple[str, str]:
    from drugpaper.chem.assembly_compat import positional_assignment, ring_position_assignment
    from drugpaper.chem.product_candidates import assemble

    if positions:
        return positional_assignment(caption, groups, positions)
    try:
        return assemble(caption, groups)["canonical_smiles"], "explicit_atom_attachment"
    except ValueError:
        result = ring_position_assignment(caption, groups)
        if result is not None:
            return result
        raise
