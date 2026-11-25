"""Assemble unnumbered product candidates from explicit, atom-labelled Markush sites."""

from __future__ import annotations

import hashlib
import itertools
import json
import math
import re
from typing import Any

from rdkit import Chem

from drugpaper.chem.esmiles import split
from drugpaper.chem.groups import abbreviations
from drugpaper.chem.normalize import canonical


def _label(text: str) -> str:
    return text.strip().lstrip("-–−").replace(" ", "")


def combinations(markush: dict[str, Any], limit: int = 128) -> tuple[list[dict[str, Any]], int]:
    options = markush.get("variable_options", {})
    raw_count = math.prod(len(v) for v in options.values())
    if not options or raw_count > 4096:
        raise ValueError("candidate_enumeration_limit")
    cases = markush.get("substitution_cases", [])
    expressions = list(dict.fromkeys(_label(c["expression"]) for c in cases))
    if not expressions:
        raise ValueError("missing_product_substitution_cases")
    result: dict[str, dict[str, Any]] = {}
    for values in itertools.product(*options.values()):
        assignment = dict(zip(options, values, strict=True))
        groups: dict[str, str] = {}
        covered: set[str] = set()
        for expression in expressions:
            matches = [
                c
                for c in cases
                if _label(c["expression"]) == expression
                and any(all(assignment.get(k) == v for k, v in a.items()) for a in c["assignments"])
            ]
            texts = {_label(c["group_text"]) for c in matches}
            if len(texts) != 1:
                raise ValueError("missing_or_ambiguous_local_case")
            groups[expression] = next(iter(texts))
            for c in matches:
                for a in c["assignments"]:
                    if all(assignment.get(k) == v for k, v in a.items()):
                        covered.update(a)
        if covered != set(options):
            raise ValueError("incomplete_variable_assignment_coverage")
        key = json.dumps(groups, sort_keys=True)
        if key not in result:
            if len(result) >= limit:
                raise ValueError("candidate_enumeration_limit")
            result[key] = {"substituents": groups, "variable_assignments": []}
        result[key]["variable_assignments"].append(assignment)
    return list(result.values()), raw_count


def assemble(caption: str, groups: dict[str, str]) -> dict[str, Any]:
    core, extension = split(caption)
    mol = Chem.MolFromSmiles(core)
    if mol is None:
        raise ValueError("invalid_scaffold")
    if re.search(r"<[rcd]>", extension):
        raise ValueError("non_atom_attachment_requires_review")
    labels = re.findall(r"<a>(\d+):([^<]+)</a>", extension)
    sites = {int(index): _label(name) for index, name in labels}
    if len(sites) != len(labels) or set(sites.values()) != set(groups):
        raise ValueError("missing_or_mismatched_attachment_labels")
    if set(sites) != {a.GetIdx() for a in mol.GetAtoms() if a.GetAtomicNum() == 0}:
        raise ValueError("attachment_labels_do_not_match_placeholders")
    rw = Chem.RWMol(mol)
    remove: list[int] = []
    known_groups = abbreviations()
    for index, name in sites.items():
        atom = mol.GetAtomWithIdx(index)
        value = groups[name]
        if atom.GetDegree() == 2 and atom.GetIsAromatic():
            if value not in {"CH", "N", "NH", "O", "S"}:
                raise ValueError("unsupported_aromatic_atom_assignment")
            replacement = Chem.Atom({"CH": 6, "N": 7, "NH": 7, "O": 8, "S": 16}[value])
            if value == "NH":
                replacement.SetNumExplicitHs(1)
                replacement.SetNoImplicit(True)
            replacement.SetIsAromatic(True)
            rw.ReplaceAtom(index, replacement)
            continue
        if atom.GetDegree() != 1 or atom.GetIsAromatic():
            raise ValueError("unsupported_attachment_topology")
        if atom.GetBonds()[0].GetBondType() != Chem.BondType.SINGLE:
            raise ValueError("unsupported_attachment_bond")
        neighbor = atom.GetNeighbors()[0].GetIdx()
        remove.append(index)
        if value == "H":
            continue
        if "Alk" in value:
            raise ValueError("unbounded_Alk_requires_explicit_definition")
        fragment_text = known_groups.get(value)
        if fragment_text is None:
            raise ValueError("unsupported_substituent_abbreviation")
        fragment = Chem.MolFromSmiles(fragment_text)
        if fragment is None or any(a.GetAtomicNum() == 0 for a in fragment.GetAtoms()):
            raise ValueError("invalid_or_unresolved_substituent")
        offset = rw.GetNumAtoms()
        rw = Chem.RWMol(Chem.CombineMols(rw, fragment))
        rw.AddBond(neighbor, offset, Chem.BondType.SINGLE)
    for index in sorted(remove, reverse=True):
        rw.RemoveAtom(index)
    product = rw.GetMol()
    Chem.SanitizeMol(product)
    if len(Chem.GetMolFrags(product)) != 1 or any(a.GetAtomicNum() == 0 for a in product.GetAtoms()):
        raise ValueError("disconnected_or_incomplete_product")
    normalized = canonical(Chem.MolToSmiles(product))
    if not normalized.valid:
        raise ValueError("invalid_assembled_product")
    return normalized.model_dump()


def materialize_products(payload: dict[str, Any]) -> list[dict[str, Any]]:
    errors: list[dict[str, Any]] = []
    for pi, pathway in enumerate(payload.get("pathways", [])):
        for si, step in enumerate(pathway.get("steps", [])):
            for ei, endpoint in enumerate(step.get("products", [])):
                markush = endpoint.get("markush")
                if not markush:
                    continue
                path = f"$.pathways[{pi}].steps[{si}].products[{ei}]"
                candidates: list[dict[str, Any]] = []
                summary = {
                    "source_reference": endpoint.get("reference"),
                    "scope": "unnumbered_candidates",
                    "status": "needs_review",
                    "candidate_count": 0,
                    "completed_count": 0,
                }
                endpoint["markush_assembly"] = summary
                endpoint["assembly_candidates"] = candidates
                try:
                    combos, raw_count = combinations(markush)
                except ValueError as exc:
                    summary["error"] = str(exc)
                    errors.append(
                        {
                            "code": "markush_candidate_enumeration_failed",
                            "field_path": path,
                            "reason": str(exc),
                        }
                    )
                    continue
                summary.update(raw_assignment_count=raw_count, candidate_count=len(combos))
                for combo in combos:
                    identity = json.dumps(combo["substituents"], sort_keys=True)
                    candidate = {
                        "candidate_id": "mc_" + hashlib.sha256((path + identity).encode()).hexdigest()[:16],
                        "reference": None,
                        "source_reference": endpoint.get("reference"),
                        "scaffold_mol_id": endpoint.get("scaffold_mol_id"),
                        **combo,
                        "attachments": list(combo["substituents"]),
                        "status": "needs_review",
                    }
                    candidates.append(candidate)
                    try:
                        scaffold = endpoint.get("scaffold_esmiles")
                        if not scaffold:
                            raise ValueError("missing_recognized_scaffold")
                        candidate["assembled_molecule"] = assemble(
                            scaffold, {k: candidate["substituents"][k] for k in candidate["attachments"]}
                        )
                        candidate["status"] = "completed"
                        candidate["assembly_provenance"] = {
                            "method": "explicit_atom_attachment",
                            "field_path": path,
                            "scaffold_esmiles": scaffold,
                            "substituents": combo["substituents"],
                            "attachments": candidate["attachments"],
                            "evidence_text": markush["evidence_text"],
                            "bindings": endpoint.get("molecular_bindings", []),
                            "number_assignment": "unassigned; not a synthesized paper member claim",
                        }
                        summary["completed_count"] += 1
                    except (ValueError, RuntimeError, IndexError) as exc:
                        candidate["error"] = str(exc)
                        errors.append(
                            {
                                "code": "markush_candidate_unresolved",
                                "field_path": path + ".markush.substitution_cases",
                                "candidate_id": candidate["candidate_id"],
                                "reason": str(exc),
                            }
                        )
                summary["failed_count"] = len(candidates) - summary["completed_count"]
                summary["status"] = "completed" if not summary["failed_count"] else "partial"
                endpoint["candidate_smiles"] = sorted(
                    {
                        c["assembled_molecule"]["canonical_smiles"]
                        for c in candidates
                        if c["status"] == "completed"
                    }
                )
    return errors
