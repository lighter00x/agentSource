from __future__ import annotations

from collections import Counter

from rdkit import Chem
from rdkit.Chem.rdMolDescriptors import CalcMolFormula

from drugpaper.chem.diff import MoleculeDiff, molecule
from drugpaper.chem.formula import composition


def ring_compositions(smiles: str) -> list[dict[str, int]]:
    mol = molecule(smiles)
    return [
        dict(Counter(mol.GetAtomWithIdx(i).GetSymbol() for i in ring))
        for ring in mol.GetRingInfo().AtomRings()
    ]


def classify(start: str, end: str, diff: MoleculeDiff | None = None) -> list[str]:
    from drugpaper.chem.diff import fragment_changes

    diff = diff or fragment_changes(start, end)
    a, b = molecule(start), molecule(end)
    result = []
    old_rings, new_rings = ring_compositions(start), ring_compositions(end)
    if len(old_rings) < len(new_rings):
        result.append("cyclization")
    elif len(old_rings) > len(new_rings):
        result.append("ring_opening")
    if len(old_rings) == len(new_rings) and old_rings != new_rings:
        result.append("ring_replacement")
    old = composition(CalcMolFormula(a)) or {}
    new = composition(CalcMolFormula(b)) or {}
    delta = {k: new.get(k, 0) - old.get(k, 0) for k in set(old) | set(new)}
    removed = {i for r in diff.regions for i in r.removed_atoms}
    added = {i for r in diff.regions for i in r.added_atoms}
    if (
        delta.get("C", 0) < 0
        and delta.get("H", 0) == 2 * delta["C"]
        and all(v == 0 for k, v in delta.items() if k not in {"C", "H"})
        and removed
        and all(a.GetAtomWithIdx(i).GetSymbol() == "C" for i in removed)
        and any(
            n.GetSymbol() == "N"
            for i in removed
            for n in a.GetAtomWithIdx(i).GetNeighbors()
            if n.GetIdx() not in removed
        )
    ):
        result.append("n_dealkylation")
    if any(delta.get(k, 0) != 0 for k in ("F", "Cl", "Br", "I")):
        result.append(
            "halogenation" if any(delta.get(k, 0) > 0 for k in ("F", "Cl", "Br", "I")) else "dehalogenation"
        )
    if (
        delta.get("C") == 1
        and delta.get("H") == 2
        and all(v == 0 for k, v in delta.items() if k not in {"C", "H"})
    ):
        result.append("methylation")
    if (
        delta.get("O") == 1
        and delta.get("H", 0) == 0
        and all(v == 0 for k, v in delta.items() if k not in {"O", "H"})
        and any(
            b.GetAtomWithIdx(i).GetSymbol() == "O" and b.GetAtomWithIdx(i).GetTotalNumHs() > 0 for i in added
        )
    ):
        result.append("hydroxylation")
    if (
        delta.get("C", 0) != 0
        and old_rings == new_rings
        and not any(t in result for t in ("n_dealkylation", "methylation"))
    ):
        result.append("chain_length_change")
    Chem.RemoveStereochemistry(a)
    Chem.RemoveStereochemistry(b)
    if not removed and not added and Chem.MolToSmiles(a) == Chem.MolToSmiles(b) and start != end:
        result.append("stereochemistry")
    return result or (["substituent_change"] if diff.regions else [])
