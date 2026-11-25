"""Resolve explicit molecular labels within one document, without guessing connectivity."""

from __future__ import annotations

import re
from typing import Any

from rdkit import Chem
from rdkit.Chem import rdCIPLabeler

from drugpaper.chem.esmiles import split
from drugpaper.chem.normalize import canonical
from drugpaper.facts.labels import normalize


def resolve_references(
    references: list[str], claims: list[dict[str, Any]], structures: list[dict[str, Any]]
) -> dict[str, Any]:
    by_id = {s["structure_id"]: s for s in structures}
    resolved = {}
    errors = []
    for reference in references:
        label = normalize(reference)
        stereo = re.fullmatch(r"\(([RS])\)-(\d+[a-z]?)", label)
        labels = {label, stereo[2]} if stereo else {label}
        matches = [
            (c, by_id[c["structure_id"]])
            for c in claims
            if normalize(c["label"]) in labels and c.get("structure_id") in by_id
        ]
        unique = {}
        contradictory = False
        for claim, candidate in matches:
            raw = candidate.get("e_smiles_raw") or candidate.get("smiles_core") or ""
            core = split(raw)[0]
            if candidate.get("is_markush") or "*" in core or not candidate.get("validation", {}).get("valid"):
                continue
            if candidate.get("source") == "opsin" and (
                candidate.get("validation", {}).get("opsin_status") == "warning"
                or candidate.get("validation", {}).get("formula_match") is not True
            ):
                continue
            mol = Chem.MolFromSmiles(core)
            if mol is None:
                continue
            derived = False
            if stereo:
                centers = Chem.FindMolChiralCenters(  # type: ignore[no-untyped-call]
                    mol, includeUnassigned=True, useLegacyImplementation=False
                )
                if len(centers) != 1:
                    continue
                index, existing = centers[0]
                if normalize(claim["label"]) == label and existing not in {"?", stereo[1]}:
                    contradictory = True
                    continue
                atom = mol.GetAtomWithIdx(index)
                atom.SetChiralTag(Chem.ChiralType.CHI_TETRAHEDRAL_CW)
                Chem.AssignStereochemistry(mol, cleanIt=True, force=True)
                rdCIPLabeler.AssignCIPLabels(mol)
                if not atom.HasProp("_CIPCode"):
                    continue
                if atom.GetProp("_CIPCode") != stereo[1]:
                    atom.InvertChirality()
                    Chem.AssignStereochemistry(mol, cleanIt=True, force=True)
                    rdCIPLabeler.AssignCIPLabels(mol)
                if atom.GetProp("_CIPCode") != stereo[1]:
                    continue
                derived = True
            smiles = Chem.MolToSmiles(mol, isomericSmiles=True)
            normalized = canonical(smiles)
            if normalized.valid and stereo:
                final_mol = Chem.MolFromSmiles(normalized.canonical_smiles)
                final_centers = Chem.FindMolChiralCenters(  # type: ignore[no-untyped-call]
                    final_mol, includeUnassigned=True, useLegacyImplementation=False
                )
                if len(final_centers) != 1 or final_centers[0][1] != stereo[1]:
                    continue
            if normalized.valid:
                unique[normalized.canonical_smiles] = {
                    **normalized.model_dump(),
                    "e_smiles_raw": smiles,
                    "structure_id": candidate["structure_id"],
                    "source_ref": candidate["node_ref"],
                    "evidence": candidate.get("evidence", []),
                    "reference": reference,
                    "binding_method": "document_reference",
                    "stereochemistry_from_explicit_label": derived,
                }
        if len(unique) != 1 or contradictory:
            errors.append({"code": "missing_or_ambiguous_reference_structure", "reference": reference})
        else:
            resolved[reference] = next(iter(unique.values()))
    return {"resolved": resolved, "errors": errors}
