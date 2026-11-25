"""Explicit R-group / aromatic atom replacement on an identifiable biaryl template."""

from __future__ import annotations

import re
from typing import Any

from rdkit import Chem

from drugpaper.chem.groups import abbreviations
from drugpaper.chem.splicer.adapter import AssemblyResult


def labeled_biaryl(caption: str, definitions: dict[str, Any]) -> AssemblyResult | None:
    if set(definitions) != {"R", "X", "Y"}:
        return None
    core, _, extension = caption.partition("<sep>")
    tags = re.findall(r"<a>(\d+):([RY])</a>", extension)
    if len(tags) != 2 or {name for _, name in tags} != {"R", "Y"}:
        return None
    ids = {name: int(index) for index, name in tags}
    mol = Chem.MolFromSmiles(core)
    if mol is None or any(i >= mol.GetNumAtoms() for i in ids.values()):
        return AssemblyResult("failed", error_code="E_INVALID_SCAFFOLD")
    rings = mol.GetRingInfo().AtomRings()
    if len(rings) != 2 or any(len(r) != 6 for r in rings) or set(rings[0]) & set(rings[1]):
        return None
    if any(not mol.GetAtomWithIdx(i).GetIsAromatic() for ring in rings for i in ring):
        return None
    r_atom, y_atom = (mol.GetAtomWithIdx(ids[key]) for key in ("R", "Y"))
    if (
        r_atom.GetAtomicNum() != 0
        or r_atom.GetDegree() != 1
        or y_atom.GetAtomicNum() != 0
        or y_atom.GetDegree() != 2
        or not y_atom.GetIsAromatic()
    ):
        return None
    if sum(a.GetAtomicNum() == 0 for a in mol.GetAtoms()) != 2:
        return AssemblyResult("failed", error_code="unsupported_labeled_scaffold")
    values = {k: str(v).strip() for k, v in definitions.items()}
    if values["R"] not in abbreviations() or values["Y"] not in {"CH", "N"}:
        return AssemblyResult("failed", error_code="unsupported_labeled_assignment")
    r_site = r_atom.GetNeighbors()[0].GetIdx()
    r_ring = next((r for r in rings if r_site in r), None)
    y_ring = next((r for r in rings if ids["Y"] in r), None)
    if not r_ring or not y_ring or r_ring == y_ring:
        return AssemblyResult("failed", error_code="unsupported_labeled_scaffold")
    bridges = [
        b
        for b in mol.GetBonds()
        if (b.GetBeginAtomIdx() in r_ring and b.GetEndAtomIdx() in y_ring)
        or (b.GetEndAtomIdx() in r_ring and b.GetBeginAtomIdx() in y_ring)
    ]
    if len(bridges) != 1 or bridges[0].GetBondType() != Chem.BondType.SINGLE:
        return AssemblyResult("failed", error_code="unsupported_labeled_scaffold")
    bridge = bridges[0]
    anchors = {
        ring: next(i for i in (bridge.GetBeginAtomIdx(), bridge.GetEndAtomIdx()) if i in ring)
        for ring in (r_ring, y_ring)
    }
    distances = Chem.GetDistanceMatrix(mol)
    # Orient the primed ring only through a unique recognized fixed substituent.
    # Its chemical identity is irrelevant; do not assume a particular side chain.
    outside = set(range(mol.GetNumAtoms())) - (set(r_ring) | set(y_ring) | {ids["R"]})
    links = [
        (i, n.GetIdx())
        for i in outside
        for n in mol.GetAtomWithIdx(i).GetNeighbors()
        if n.GetIdx() in set(r_ring) | set(y_ring)
    ]
    if not outside or len(links) != 1 or links[0][1] not in y_ring:
        return AssemblyResult("failed", error_code="unsupported_labeled_scaffold")
    landmark = links[0][1]
    if distances[anchors[y_ring], landmark] != 1:
        return AssemblyResult("failed", error_code="ambiguous_ring_numbering")
    visited = {links[0][0]}
    pending = list(visited)
    while pending:
        for neighbor in mol.GetAtomWithIdx(pending.pop()).GetNeighbors():
            i = neighbor.GetIdx()
            if i in outside and i not in visited:
                visited.add(i)
                pending.append(i)
    if visited != outside:
        return AssemblyResult("failed", error_code="unsupported_labeled_scaffold")
    for atom in mol.GetAtoms():
        atom.SetIntProp("source_index", atom.GetIdx())
    base = Chem.RWMol(mol)
    replacement = Chem.Atom(6 if values["Y"] == "CH" else 7)
    replacement.SetIsAromatic(True)
    replacement.SetIntProp("source_index", ids["Y"])
    base.ReplaceAtom(ids["Y"], replacement)
    group = Chem.MolFromSmiles(abbreviations()[values["R"]])
    offset = base.GetNumAtoms()
    base = Chem.RWMol(Chem.CombineMols(base, group))
    base.AddBond(r_site, offset, Chem.BondType.SINGLE)
    base.RemoveAtom(ids["R"])
    if values["X"] == "H":
        targets: list[int | None] = [None]
    else:
        match = re.fullmatch(r"([2-6])(['′’]?)-(F|Cl|Br|I)", values["X"])
        if not match:
            return AssemblyResult("failed", error_code="unsupported_labeled_assignment")
        ring = y_ring if match[2] else r_ring
        position = int(match[1])
        if ring == y_ring:
            order = [anchors[y_ring], landmark]
            while len(order) < 6:
                neighbors = [
                    a.GetIdx()
                    for a in mol.GetAtomWithIdx(order[-1]).GetNeighbors()
                    if a.GetIdx() in y_ring and a.GetIdx() not in order
                ]
                if len(neighbors) != 1:
                    return AssemblyResult("failed", error_code="ambiguous_ring_numbering")
                order.append(neighbors[0])
            targets = [order[position - 1]]
        else:
            targets = [i for i in r_ring if distances[anchors[r_ring], i] == min(position - 1, 7 - position)]
    products = set()
    for target in targets:
        assembled = Chem.RWMol(base)
        if target is not None:
            current = next(
                a.GetIdx()
                for a in assembled.GetAtoms()
                if a.HasProp("source_index") and a.GetIntProp("source_index") == target
            )
            atom = assembled.GetAtomWithIdx(current)
            if atom.GetDegree() != 2 or atom.GetAtomicNum() != 6:
                return AssemblyResult("failed", error_code="occupied_positional_site")
            chlorine = assembled.AddAtom(Chem.Atom(Chem.GetPeriodicTable().GetAtomicNumber(match[3])))
            assembled.AddBond(current, chlorine, Chem.BondType.SINGLE)
        candidate = assembled.GetMol()
        try:
            Chem.SanitizeMol(candidate)
            products.add(Chem.MolToSmiles(candidate))
        except (ValueError, RuntimeError):
            return AssemblyResult("failed", error_code="E_NO_VALID_PRODUCT")
    if len(products) != 1:
        return AssemblyResult("failed", error_code="ambiguous_positional_product")
    return AssemblyResult(
        "completed",
        sorted(products),
        message="Explicit R terminal substitution, Y aromatic CH/N replacement, and printed X ring position; stereochemistry unspecified.",
    )
