"""Atom-indexed MCS differences and executable region replacements."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field
from rdkit import Chem
from rdkit.Chem import rdFMCS


class FragmentChange(BaseModel):
    original_atoms: list[int] = Field(default_factory=list)
    replacement_atoms: list[int] = Field(default_factory=list)
    removed_atoms: list[int] = Field(default_factory=list)
    added_atoms: list[int] = Field(default_factory=list)
    original_fragment: str = ""
    replacement_fragment: str = ""
    substring_match: bool = False


class MoleculeDiff(BaseModel):
    start: str
    end: str
    mapping: list[tuple[int, int]] = Field(default_factory=list)
    regions: list[FragmentChange] = Field(default_factory=list)
    timed_out: bool = False
    warnings: list[str] = Field(default_factory=list)


def molecule(smiles: str) -> Any:
    mol = Chem.MolFromSmiles(smiles.split("<sep>", 1)[0])
    if mol is None or any(a.GetAtomicNum() == 0 for a in mol.GetAtoms()):
        raise ValueError("concrete sanitized SMILES required")
    return mol


def _components(mol: Any, indices: set[int]) -> list[set[int]]:
    result = []
    todo = set(indices)
    while todo:
        connected = {todo.pop()}
        frontier = set(connected)
        while frontier:
            nxt = {n.GetIdx() for i in frontier for n in mol.GetAtomWithIdx(i).GetNeighbors()} & todo
            todo -= nxt
            connected |= nxt
            frontier = nxt
        result.append(connected)
    return result


def fragment(mol: Any, atoms: list[int]) -> str:
    return (
        str(Chem.MolFragmentToSmiles(mol, atomsToUse=sorted(atoms), canonical=True, isomericSmiles=True))
        if atoms
        else ""
    )


def fragment_changes(start: str, end: str, *, timeout: int = 10) -> MoleculeDiff:
    a, b = molecule(start), molecule(end)
    result = MoleculeDiff(start=start, end=end)
    common = rdFMCS.FindMCS(
        [a, b], ringMatchesRingOnly=True, completeRingsOnly=True, matchValences=True, timeout=timeout
    )
    if common.canceled:
        result.timed_out = True
        result.warnings.append("MCS timeout")
        return result
    query = Chem.MolFromSmarts(common.smartsString)
    left = a.GetSubstructMatch(query) if query is not None and common.numAtoms else ()
    right = b.GetSubstructMatch(query) if query is not None and common.numAtoms else ()
    result.mapping = list(zip(left, right, strict=True))
    a_to_b = dict(result.mapping)
    b_to_a = {j: i for i, j in result.mapping}
    groups: list[dict[str, set[int]]] = []
    for side, mol, retained in (("a", a, set(left)), ("b", b, set(right))):
        for component in _components(mol, set(range(mol.GetNumAtoms())) - retained):
            anchors = {n.GetIdx() for i in component for n in mol.GetAtomWithIdx(i).GetNeighbors()} & retained
            if side == "b":
                anchors = {b_to_a[i] for i in anchors}
            # No common atoms is one whole-molecule replacement, never a fictitious scaffold.
            overlaps = [g for g in groups if g["anchors"] & anchors or not result.mapping]
            group = {
                "a": component if side == "a" else set(),
                "b": component if side == "b" else set(),
                "anchors": anchors,
            }
            for old in overlaps:
                for key in group:
                    group[key] |= old[key]
                groups.remove(old)
            groups.append(group)
    if not groups and Chem.MolToSmiles(a, isomericSmiles=True) != Chem.MolToSmiles(b, isomericSmiles=True):
        # Stereochemistry/bond-state changes have no added/deleted atoms.
        groups = [{"a": set(), "b": set(), "anchors": set(left)}]
    for group in groups:
        context_a = group["a"] | group["anchors"]
        context_b = group["b"] | {a_to_b[i] for i in group["anchors"]}
        fa, fb = fragment(a, sorted(context_a)), fragment(b, sorted(context_b))
        result.regions.append(
            FragmentChange(
                original_atoms=sorted(context_a),
                replacement_atoms=sorted(context_b),
                removed_atoms=sorted(group["a"]),
                added_atoms=sorted(group["b"]),
                original_fragment=fa,
                replacement_fragment=fb,
                substring_match=fa in start and fb in end,
            )
        )
    return result


def replace_regions(diff: MoleculeDiff, regions: list[FragmentChange] | None = None) -> str:
    """Remove exactly the selected start atoms and insert the mapped end region(s)."""
    if diff.timed_out:
        raise ValueError("cannot reconstruct timed-out MCS")
    regions = diff.regions if regions is None else regions
    a, b = molecule(diff.start), molecule(diff.end)
    removed = {i for r in regions for i in r.removed_atoms}
    added = {i for r in regions for i in r.added_atoms}
    anchors = {i for r in regions for i in r.original_atoms} - removed
    rw = Chem.RWMol(a)
    for i in sorted(removed, reverse=True):
        rw.RemoveAtom(i)
    old_to_new = {old: new for new, old in enumerate(i for i in range(a.GetNumAtoms()) if i not in removed)}
    b_to_new = {j: old_to_new[i] for i, j in diff.mapping if i in old_to_new}
    for j in sorted(added):
        b_to_new[j] = rw.AddAtom(Chem.Atom(b.GetAtomWithIdx(j)))
    for i, j in diff.mapping:
        if i in anchors and i in old_to_new:
            source, target = b.GetAtomWithIdx(j), rw.GetAtomWithIdx(old_to_new[i])
            target.SetFormalCharge(source.GetFormalCharge())
            target.SetNumExplicitHs(source.GetNumExplicitHs())
            target.SetNoImplicit(source.GetNoImplicit())
            target.SetChiralTag(source.GetChiralTag())
    for bond in b.GetBonds():
        i, j = bond.GetBeginAtomIdx(), bond.GetEndAtomIdx()
        if i in b_to_new and j in b_to_new and (i in added or j in added):
            rw.AddBond(b_to_new[i], b_to_new[j], bond.GetBondType())
    product = rw.GetMol()
    Chem.SanitizeMol(product)
    return str(Chem.MolToSmiles(product, isomericSmiles=True))


def virtual_intermediates(diff: MoleculeDiff) -> list[str]:
    if len(diff.regions) < 2:
        return []
    a = molecule(diff.start)
    ordered = sorted(
        diff.regions,
        key=lambda r: (
            -sum(a.GetAtomWithIdx(i).IsInRing() for i in r.removed_atoms),
            -len(r.removed_atoms),
            r.removed_atoms,
        ),
    )
    result = []
    for index in range(1, len(ordered)):
        result.append(replace_regions(diff, ordered[:index]))
    return result
