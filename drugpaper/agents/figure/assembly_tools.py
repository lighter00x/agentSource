"""Figure-owned assembly tools: inspect, trial, and apply checked ID-only corrections."""

from __future__ import annotations

import copy
import hashlib
import json
import re
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from drugpaper.agents.figure.context import current_structure_inventory, pointer
from drugpaper.chem.assembly import ALIASES, normalize_group
from drugpaper.facts.compound_instances import CompoundInstance
from drugpaper.facts.molecular_delivery import deliver

MAX_ATTEMPTS = 32
INDEX = r"(?:0|[1-9][0-9]*)"
INSTANCE_PATH = re.compile(
    rf"/(?:compound_instances/{INDEX}|pathways/{INDEX}/steps/{INDEX}/products/{INDEX}/variants/{INDEX}|optimization_steps/{INDEX}/variants/{INDEX})\Z"
)


class StrictRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class InspectAssembly(StrictRequest):
    group_queries: list[str] = Field(default_factory=list, max_length=16)


class InspectAttachmentSites(StrictRequest):
    mol_ids: list[int] = Field(default_factory=list, max_length=16)


def inspect_attachment_sites(materials: Any, extraction: dict, args: dict) -> dict:
    """Expose recognition attachment labels and validate explicit selections.

    Labels are site names only; no recognized chemical strings are disclosed.
    This is available before backfill so members are not submitted with selectors
    that can only fail later in the deterministic assembly stage.
    """
    req = InspectAttachmentSites.model_validate(args)
    inventory = current_structure_inventory(materials)
    labels_by_mol = {
        s["mol_id"]: list(s.get("attachment_labels") or [])
        for s in inventory.get("structures", [])
        if isinstance(s.get("mol_id"), int)
    }
    requested = set(req.mol_ids) or None
    instances: dict[int, list[dict]] = {}

    def walk(value: Any, path: str = "") -> None:
        if isinstance(value, dict):
            mol_id = value.get("scaffold_mol_id")
            groups = value.get("substituents")
            if isinstance(mol_id, int) and isinstance(groups, dict) and groups:
                instances.setdefault(mol_id, []).append(
                    {"path": path, "attachments": value.get("attachments")}
                )
            for key, child in value.items():
                walk(child, path + "/" + key)
        elif isinstance(value, list):
            for index, child in enumerate(value):
                walk(child, path + f"/{index}")

    walk(extraction)
    sites = []
    for mol_id in sorted(labels_by_mol):
        if requested and mol_id not in requested:
            continue
        labels = labels_by_mol[mol_id]
        rows = []
        for instance in instances.get(mol_id, []):
            selected = instance["attachments"]
            missing: list[str] = []
            extra: list[str] = []
            if not labels:
                status = "no_attachment_sites"
            elif not isinstance(selected, list) or not selected:
                status = "missing_attachments"
                missing = list(labels)
            else:
                extra = [key for key in selected if key not in labels]
                missing = [label for label in labels if label not in selected]
                if extra:
                    status = "invalid_attachments"
                elif missing:
                    status = "incomplete_attachments"
                else:
                    status = "ok"
            rows.append(
                {
                    "path": instance["path"],
                    "attachments": selected,
                    "status": status,
                    "missing_labels": missing,
                    "unexpected_keys": extra,
                }
            )
        sites.append(
            {
                "mol_id": mol_id,
                "attachment_labels": labels,
                "status": "no_attachment_sites" if not labels else "available",
                "instances": rows,
            }
        )
    unknown = sorted(requested - set(labels_by_mol)) if requested else []
    return {
        "ok": True,
        "sites": sites,
        "unknown_mol_ids": unknown,
        "rule": (
            "attachments must exactly select all attachment_labels of this scaffold. "
            "Labels are recognition site names, not chemical structures. A scaffold with "
            "no attachment labels cannot be used for assembly: choose a Markush-recognized "
            "scaffold or record an unsupported_representation gap. Do not invent labels."
        ),
    }


class AssemblyItem(StrictRequest):
    instance_path: str = Field(max_length=250)
    substituents: dict[str, str | int] | None = None
    attachments: list[str] | None = Field(default=None, min_length=1, max_length=64)
    position_definitions: dict[str, str] | None = None
    evidence_ids: list[str] = Field(default_factory=list, max_length=16)
    note: str = Field(default="", max_length=2000)


class TryAssembly(StrictRequest):
    items: list[AssemblyItem] = Field(min_length=1, max_length=8)


class ApplyAssembly(StrictRequest):
    attempt_ids: list[str] = Field(min_length=1, max_length=8)


def _owned(materials: Any) -> tuple[list[dict], list[dict]]:
    target = materials.target
    boxes = []
    for n in target.get("children", []):
        index = n.get("attributes", {}).get("reading_index")
        if n.get("status") != "removed" and type(index) is int and n["ref"].startswith(target["ref"] + "."):
            boxes.append(
                {
                    "mol_id": index,
                    "node_ref": n["ref"],
                    "segment": n.get("geometry", {}).get("parent_region_index", 0),
                }
            )
    refs = {b["node_ref"] for b in boxes}
    return boxes, [s for s in target.get("structures", []) if s.get("node_ref") in refs]


def _fingerprint(materials: Any) -> str:
    return hashlib.sha256(json.dumps(_owned(materials), sort_keys=True, default=str).encode()).hexdigest()


def inspect_assembly(materials: Any, args: dict) -> dict:
    req = InspectAssembly.model_validate(args)
    from rdkit import Chem

    from drugpaper.chem.assembly_compat import capabilities
    from drugpaper.chem.esmiles import split
    from drugpaper.chem.groups import abbreviations
    from drugpaper.chem.splicer.adapter import infer_role

    known = set(abbreviations()) | {"H", "CH", "N", "NH", "O", "S"}
    inventory = current_structure_inventory(materials)
    boxes, structures = _owned(materials)
    by_ref = {b["node_ref"]: b["mol_id"] for b in boxes}
    details = []
    for structure in structures:
        raw = structure.get("e_smiles_raw") or ""
        core, annotations = split(raw)
        mol = Chem.MolFromSmiles(core)
        sites = []
        if mol is not None:
            for index, label in re.findall(r"<a>(\d+):([^<]+)</a>", annotations):
                if int(index) < mol.GetNumAtoms():
                    atom = mol.GetAtomWithIdx(int(index))
                    sites.append(
                        {
                            "label": label,
                            "atom_index": int(index),
                            "degree": atom.GetDegree(),
                            "aromatic": atom.GetIsAromatic(),
                            "placeholder": atom.GetAtomicNum() == 0,
                        }
                    )
        details.append(
            {"mol_id": by_ref[structure["node_ref"]], "inferred_role": infer_role(raw), "sites": sites}
        )
    return {
        "ok": True,
        "inventory": inventory,
        "structure_details": details,
        "aliases": ALIASES,
        "groups": [
            {"input": v, "normalized": normalize_group(v), "supported": normalize_group(v) in known}
            for v in req.group_queries
        ],
        "strategies": [
            "explicit_atom_attachment",
            "current_figure_fragment_attachment",
            "explicit_positional_assignment",
            "explicit_labeled_ring_position",
        ],
        "capabilities": capabilities(),
        "position_contract": {
            "scope": "Preserve the paper's position meaning and raw source text in evidence/provenance. Computation uses explicit values. Select a compatible graph/numbering adapter from capabilities; unknown or ambiguous mappings require review. No mandatory variable names or arbitrary relocation.",
            "values": "Use explicit source locants or locanted group values, not explanatory sentences. Keep explanations in evidence_text. If an older registered position contains prose, obtain its exact supported value from the actual source and submit an audited record_reference_assignments correction with supersedes and correction_reason; do not silently drop it or declare a representation limitation without trying this correction.",
        },
        "rules": "Use existing instance_path and current mol IDs only. Trial is not application. Provide actual applied evidence and note for semantic assignment changes. Do not strip position prefixes or invent Alk. Validate after apply.",
        "attachment_contract": "Each new assembled member must provide attachments: a unique nonempty list selecting keys in substituents that match actual scaffold sites. Only selected values participate; other fields remain explanatory. Backfill preserves the list and values. Invalid selection fails without guessing. Old records without selectors use labelled legacy compatibility; correct them in structure before new FigureAgent delivery.",
        "input_projection": "Explicit attachments are authoritative: only selected keys are read. Supported position normalization remains; no explicit selector is rewritten or auto-renamed. Legacy records without attachments retain the old labelled projection. Inspect assembly_input_projection and attachments in provenance.",
    }


def try_assembly(materials: Any, extraction: dict, log: dict, args: dict) -> dict:
    req = TryAssembly.model_validate(args)
    attempts = log.setdefault("assembly_attempts", [])
    if len(attempts) + len(req.items) > MAX_ATTEMPTS:
        raise ValueError("Assembly attempt budget exhausted")
    boxes, structures = _owned(materials)
    known = {e["id"] for e in log.get("evidence", []) if e.get("tool") != "preview_pages"}
    applied = set(log.get("applied_evidence_ids", []))
    results = []
    for item in req.items:
        record = {
            "attempt_id": f"a{len(attempts) + 1}",
            "instance_path": item.instance_path,
            "evidence_ids": item.evidence_ids,
            "note": item.note,
            "status": "failed",
        }
        try:
            if not INSTANCE_PATH.fullmatch(item.instance_path):
                raise ValueError(
                    "Only existing product variants or explicit compound_instances can be repaired"
                )
            original = pointer(extraction, item.instance_path)
            instance = CompoundInstance.model_validate(original)
            if instance.scaffold_mol_id is None:
                raise ValueError("Directly recognized molecules cannot be replaced by assembly tools")
            candidate = instance.model_dump()
            if item.substituents is not None:
                candidate["substituents"] = item.substituents
            if item.position_definitions is not None:
                candidate["position_definitions"] = item.position_definitions
            if item.attachments is not None:
                candidate["attachments"] = item.attachments
            before = {
                k: normalize_group(v) if isinstance(v, str) else v for k, v in instance.substituents.items()
            }
            after = {
                k: normalize_group(v) if isinstance(v, str) else v
                for k, v in candidate["substituents"].items()
            }
            changed = (
                before != after
                or candidate["position_definitions"] != instance.position_definitions
                or candidate.get("attachments") != instance.attachments
            )
            if any(i not in known or i not in applied for i in item.evidence_ids):
                raise ValueError(
                    "Use actual applied text/detail evidence IDs; previews and invented IDs are forbidden"
                )
            if changed and (not item.evidence_ids or not item.note.strip()):
                raise ValueError("Semantic changes require applied evidence IDs and an explanation")
            candidate["substituents"] = after
            from drugpaper.chem.assembly_inputs import preserves_assignment

            labels = next(
                (
                    s["attachment_labels"]
                    for s in current_structure_inventory(materials)["structures"]
                    if s["mol_id"] == instance.scaffold_mol_id
                ),
                [],
            )
            for source_record in log.get("reference_assignments", []):
                if not item.instance_path.startswith(source_record["product_path"] + "/variants/"):
                    continue
                for required in source_record["assignments"]:
                    if required["reference"] != candidate["reference"]:
                        continue
                    if not preserves_assignment(labels, required, candidate):
                        raise ValueError(
                            "Trial conflicts with registered reference assignments; preserve required substituent and position fields. "
                            f"If the source record itself is wrong, inspect its image and use record_reference_assignments with supersedes naming source record {source_record.get('supplement_id', '(inspect workflow source_records for its ID)')} and correction_reason; do not bypass provenance with a trial."
                        )
            positional_value = any(
                isinstance(v, str) and re.fullmatch(r"[2-6]['′’]?-Cl", v) for v in after.values()
            )
            representation_gaps = [
                g
                for g in extraction.get("information_gaps", [])
                if g.get("category") == "unsupported_representation"
                and any(
                    item.instance_path == p or item.instance_path.startswith(p + "/")
                    for p in g.get("field_paths", [])
                )
            ]
            if representation_gaps and not candidate["position_definitions"] and not positional_value:
                raise ValueError(
                    "Known representation gap: supply evidence-backed position definitions or leave for review; normalizing a group does not fix positions"
                )
            if changed:
                candidate["evidence_text"] += (
                    f"; Assembly correction [{','.join(item.evidence_ids)}]: {item.note}"
                )
            candidate = CompoundInstance.model_validate(candidate).model_dump()
            from drugpaper.agents.figure.reference_assignments import unproven_member_paths

            trial_extraction = copy.deepcopy(extraction)
            parent = trial_extraction
            parts = item.instance_path[1:].split("/")
            for key in parts[:-1]:
                parent = parent[int(key)] if isinstance(parent, list) else parent[key]
            parent[int(parts[-1])] = candidate
            if item.instance_path in unproven_member_paths(
                trial_extraction, log.get("reference_assignments", [])
            ):
                raise ValueError(
                    "Member identity is unproven: locate relevant visual information and record per-member assignments before reusing the same template for distinct labels"
                )
            required_ids = {candidate["scaffold_mol_id"]} | {
                value for value in candidate["substituents"].values() if type(value) is int
            }
            trial_boxes = [box for box in boxes if box["mol_id"] in required_ids]
            trial_refs = {box["node_ref"] for box in trial_boxes}
            delivered = deliver(
                {"compound_instances": [candidate]},
                trial_boxes,
                [structure for structure in structures if structure["node_ref"] in trial_refs],
            )
            member = delivered["payload"]["compound_instances"][0]
            record.update(
                original=original,
                candidate=candidate,
                structure_fingerprint=_fingerprint(materials),
                errors=delivered["errors"],
                normalized_substituents=after,
            )
            if member.get("assembly_status") == "completed":
                record.update(
                    status="completed",
                    assembled_molecule=member["assembled_molecule"],
                    provenance=member["assembly_provenance"],
                )
            else:
                record["error"] = next(
                    (e.get("reason", e.get("code")) for e in delivered["errors"]), "assembly_failed"
                )
        except (ValueError, TypeError, KeyError) as exc:
            record["error"] = str(exc)
        if record["status"] != "completed":
            error = record.get("error", "")
            record["recovery_hint"] = (
                "The position_definitions input is not computationally supported. Read its exact source locant; keep prose in evidence_text. If this is an existing source record, correct it with record_reference_assignments using workflow source_records IDs in supersedes plus correction_reason, then retry. Do not infer a locant from vague prose."
                if error == "unsupported_positional_definition"
                else "Use inspect_assembly to check a closed alias; unknown groups require explicit evidence/current detected fragment, never arbitrary SMILES."
                if "abbreviation" in error or "literal" in error
                else "Inspect current attachment labels and source evidence; supply supported explicit positions if justified, otherwise retain review."
                if "position" in error or "representation" in error or "attachment" in error
                else "Check the indicated instance path, current-figure IDs and applied evidence. Do not repeat the same failed inputs."
            )
        attempts.append(copy.deepcopy(record))
        # ID-only candidate is safe; trusted computed product is never injected into visual Skill inputs.
        results.append({k: v for k, v in record.items() if k not in {"original", "structure_fingerprint"}})
    return {
        "ok": True,
        "results": results,
        "remaining_attempts": MAX_ATTEMPTS - len(attempts),
        "note": "Only completed attempts can be applied; inspect individual status and errors.",
    }


def apply_assembly(materials: Any, extraction: dict, log: dict, args: dict) -> dict:
    req = ApplyAssembly.model_validate(args)
    if len(set(req.attempt_ids)) != len(req.attempt_ids):
        raise ValueError("Duplicate assembly attempt IDs")
    by_id = {a["attempt_id"]: a for a in log.get("assembly_attempts", [])}
    selected = [by_id.get(i) for i in req.attempt_ids]
    paths = set()
    for a in selected:
        if not a or a["status"] != "completed":
            raise ValueError("Only completed local trial attempts can be applied")
        if a["instance_path"] in paths:
            raise ValueError("Only one correction per instance in a batch")
        paths.add(a["instance_path"])
        if (
            a.get("applied")
            or pointer(extraction, a["instance_path"]) != a["original"]
            or _fingerprint(materials) != a["structure_fingerprint"]
        ):
            raise ValueError("Stale trial: instance or current recognition changed; try again")
        known = {e["id"] for e in log.get("evidence", []) if e.get("tool") != "preview_pages"}
        if not set(a["evidence_ids"]) <= (known & set(log.get("applied_evidence_ids", []))):
            raise ValueError("Trial evidence is no longer applied")
    # Validate entire batch before mutating any instance. Only ID-only specs, not products, are written.
    for a in selected:
        keys = a["instance_path"][1:].split("/")
        parent = extraction
        for k in keys[:-1]:
            parent = parent[int(k)] if isinstance(parent, list) else parent[k]
        parent[int(keys[-1])] = copy.deepcopy(a["candidate"])
        a["applied"] = True
    return {
        "ok": True,
        "applied_attempt_ids": req.attempt_ids,
        "next_step": "validate_result",
        "note": "Recompute final products through normal backfill; trial products do not bypass validation.",
    }
