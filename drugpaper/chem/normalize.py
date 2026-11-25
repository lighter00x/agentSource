from __future__ import annotations

from pydantic import BaseModel
from rdkit import Chem
from rdkit.Chem.rdMolDescriptors import CalcMolFormula


class CanonicalResult(BaseModel):
    valid: bool
    canonical_smiles: str | None = None
    inchikey: str | None = None
    formula: str | None = None
    error: str | None = None


def canonical(smiles_core: str) -> CanonicalResult:
    try:
        mol = Chem.MolFromSmiles(smiles_core, sanitize=False) if smiles_core.strip() else None
        if mol is None:
            return CanonicalResult(valid=False, error="invalid_smiles")
        Chem.SanitizeMol(mol)
        mol = Chem.RemoveHs(mol)
        return CanonicalResult(
            valid=True,
            canonical_smiles=Chem.MolToSmiles(mol, canonical=True, isomericSmiles=True),
            inchikey=Chem.MolToInchiKey(mol) or None,  # type: ignore[no-untyped-call]
            formula=CalcMolFormula(mol),
        )
    except (ValueError, RuntimeError) as exc:
        return CanonicalResult(valid=False, error=str(exc))
