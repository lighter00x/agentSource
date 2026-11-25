"""Conservative positional substitution for explicitly defined biaryl aldehyde SARs."""

from __future__ import annotations

import re
from itertools import product
from typing import Any

from rdkit import Chem

from drugpaper.chem.splicer.adapter import AssemblyResult


def biaryl_aldehyde(caption: str, definitions: dict[str, Any]) -> AssemblyResult:
    keys = {"CHO position", "XCHYZ position", "X", "Y, Z"}
    if set(definitions) != keys:
        return AssemblyResult("skipped", error_code="positional_variable")
    values = {key: str(value).strip() for key, value in definitions.items()}
    first = re.fullmatch(r"([2-6])", values["CHO position"])
    second = re.fullmatch(r"([2-6])['′’]", values["XCHYZ position"])
    yz = [v.strip() for v in values["Y, Z"].split(",")]
    if not first or not second or values["X"] not in {"O", "S"} or yz not in [["H", "F"], ["F", "F"]]:
        return AssemblyResult("failed", error_code="unsupported_positional_assignment")
    mol = Chem.MolFromSmiles(caption.split("<sep>", 1)[0])
    if mol is None:
        return AssemblyResult("failed", error_code="E_INVALID_SCAFFOLD")
    rings = mol.GetRingInfo().AtomRings()
    if len(rings) != 2 or any(len(r) != 6 for r in rings) or set(rings[0]) & set(rings[1]):
        return AssemblyResult("failed", error_code="unsupported_positional_scaffold")
    ring_atoms = set(rings[0]) | set(rings[1])
    if any(
        not mol.GetAtomWithIdx(i).GetIsAromatic() or mol.GetAtomWithIdx(i).GetSymbol() != "C"
        for i in ring_atoms
    ):
        return AssemblyResult("failed", error_code="unsupported_positional_scaffold")
    extra = set(range(mol.GetNumAtoms())) - ring_atoms
    aldehydes = mol.GetSubstructMatches(Chem.MolFromSmarts("[CX3H1](=O)-[c]"))
    if len(extra) != 2 or len(aldehydes) != 1 or set(aldehydes[0][:2]) != extra:
        return AssemblyResult("failed", error_code="unsupported_positional_scaffold")
    cho_ring = next(r for r in rings if aldehydes[0][2] in r)
    # Preserve ring identity through atom deletion; numbering origin is the inter-ring bond.
    for atom in mol.GetAtoms():
        if atom.GetIdx() in ring_atoms:
            atom.SetIntProp("sar_ring", 0 if atom.GetIdx() in cho_ring else 1)
    rw = Chem.RWMol(mol)
    for index in sorted(extra, reverse=True):
        rw.RemoveAtom(index)
    core = rw.GetMol()
    Chem.SanitizeMol(core)
    bridges = [
        b
        for b in core.GetBonds()
        if b.GetBeginAtom().GetIntProp("sar_ring") != b.GetEndAtom().GetIntProp("sar_ring")
    ]
    if len(bridges) != 1 or bridges[0].GetBondType() != Chem.BondType.SINGLE:
        return AssemblyResult("failed", error_code="unsupported_positional_scaffold")
    anchors = {
        a.GetIntProp("sar_ring"): a.GetIdx() for a in (bridges[0].GetBeginAtom(), bridges[0].GetEndAtom())
    }
    distances = Chem.GetDistanceMatrix(core)
    choices = [
        [
            a.GetIdx()
            for a in core.GetAtoms()
            if a.GetIntProp("sar_ring") == ring
            and distances[anchors[ring], a.GetIdx()] == min(int(position) - 1, 7 - int(position))
        ]
        for ring, position in enumerate((first[1], second[1]))
    ]
    fragment = values["X"] + ("CF" if yz == ["H", "F"] else "C(F)F")
    products = set()
    for cho_atom, group_atom in product(*choices):
        assembled = Chem.RWMol(core)
        for target, smiles in ((cho_atom, "C=O"), (group_atom, fragment)):
            substituent = Chem.MolFromSmiles(smiles)
            offset = assembled.GetNumAtoms()
            assembled = Chem.RWMol(Chem.CombineMols(assembled, substituent))
            assembled.AddBond(target, offset, Chem.BondType.SINGLE)
        candidate = assembled.GetMol()
        try:
            Chem.SanitizeMol(candidate)
            products.add(Chem.MolToSmiles(candidate, canonical=True))
        except (ValueError, RuntimeError):
            return AssemblyResult("failed", error_code="E_NO_VALID_PRODUCT")
    if len(products) != 1:
        return AssemblyResult("failed", error_code="ambiguous_positional_product")
    return AssemblyResult(
        "completed",
        sorted(products),
        message="Explicit CHO/XCHYZ table positions relative to the biaryl bond; recognition ring labels are not used as attachment positions.",
    )
