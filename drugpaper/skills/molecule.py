"""P4 Python Skills：工具调用、确定性代码与事实提议组合。"""

from __future__ import annotations

import asyncio
import base64
import json
from typing import Any

from drugpaper.chem.esmiles import split
from drugpaper.chem.normalize import canonical
from drugpaper.chem.roles import infer_from_esmiles
from drugpaper.domain.geometry import BBox, local_to_page
from drugpaper.domain.ids import new_id
from drugpaper.facts.backfill import backfill
from drugpaper.facts.compounds import StructureCandidate
from drugpaper.facts.label_geometry import candidates
from drugpaper.facts.legacy_molecule import Molecule
from drugpaper.facts.resolver import resolve
from drugpaper.skills.protocol import (
    ArtifactDraft,
    FactProposal,
    NodeProposal,
    SkillContext,
    SkillQuality,
    SkillResult,
)
from drugpaper.vision.boxes import is_whole_image, match_ids, nested_mask_regions, pad_relative, reading_order
from drugpaper.vision.som import crop_masked, render


def target(ctx: SkillContext) -> dict[str, Any]:
    if ctx.materials is None:
        raise ValueError("缺少任务材料")
    return ctx.materials.target


def outcome(
    kind: str,
    payload: dict[str, Any],
    facts: list[FactProposal] | None = None,
    nodes: list[NodeProposal] | None = None,
    review: bool = False,
) -> SkillResult:
    return SkillResult(
        artifacts=[
            ArtifactDraft(
                artifact_type=kind, payload=payload, status="needs_review" if review else "completed"
            )
        ],
        fact_proposals=facts or [],
        node_proposals=nodes or [],
        quality=SkillQuality(status="needs_review" if review else "ok"),
    )


async def detect(inputs: dict[str, Any], ctx: SkillContext) -> SkillResult:
    material = target(ctx)
    if ctx.blobs is None and material.get("regions"):
        raise ValueError("缺少资产存储")
    local = ctx.target_ref.rsplit("/", 1)[-1]
    old = material.get("children", [])
    next_id = max([int(n["local_id"].rsplit(".m", 1)[-1]) for n in old] or [-1]) + 1
    proposals: list[NodeProposal] = []
    boxes_payload: list[dict[str, Any]] = []
    whole: list[bool] = []
    image_refs: list[str] = []
    provenance: list[dict[str, Any]] = []
    for region_index, _ in enumerate(material.get("regions", [])):
        result = await ctx.tools.call(
            "molecule.detect",
            target_ref=ctx.target_ref,
            parent_region_index=region_index,
            preset=inputs.get("preset", "default"),
        )
        assert ctx.blobs is not None
        report = json.loads(await ctx.blobs.get(result["result_ref"]))["report"]
        image_refs.append(result["image_ref"])
        provenance.append(result["provenance"])
        detections = [
            d
            for d in report["detections"]
            if BBox(
                x0=d["bbox_norm_xyxy"][0],
                y0=d["bbox_norm_xyxy"][1],
                x1=d["bbox_norm_xyxy"][2],
                y1=d["bbox_norm_xyxy"][3],
            ).width
            >= inputs.get("min_ratio", 0.015)
            and (d["bbox_norm_xyxy"][3] - d["bbox_norm_xyxy"][1]) >= inputs.get("min_ratio", 0.015)
        ]
        raw = [
            BBox(
                x0=d["bbox_norm_xyxy"][0],
                y0=d["bbox_norm_xyxy"][1],
                x1=d["bbox_norm_xyxy"][2],
                y1=d["bbox_norm_xyxy"][3],
            )
            for d in detections
        ]
        whole.append(is_whole_image(raw))
        padded = [pad_relative(b) for b in raw]
        masks = nested_mask_regions(padded)
        previous = {
            n["local_id"]: BBox.model_validate(n["geometry"]["local_bbox"])
            for n in old
            if n["geometry"].get("parent_region_index") == region_index
        }
        matched = match_ids(padded, previous)
        for i in reading_order(raw):
            node_id = matched.get(i)
            if node_id is None:
                node_id = f"{local}.m{next_id:03d}"
                next_id += 1
            attrs = {
                "confidence": detections[i]["confidence"],
                "cls": detections[i]["class_id"],
                "reading_index": len(boxes_payload),
                "masked_regions": [m.model_dump() for m in masks[i]],
                "is_scaffold_candidate": False,
            }
            box_record = {
                "local_id": node_id,
                "local_bbox": padded[i].model_dump(),
                "parent_region_index": region_index,
                **attrs,
            }
            boxes_payload.append(box_record)
            proposals.append(
                NodeProposal(
                    local_id=node_id,
                    node_type="molecule",
                    parent_local_id=local,
                    local_bbox=padded[i].as_tuple(),
                    parent_region_index=region_index,
                    attributes=attrs,
                )
            )
    if material.get("type") == "table" and proposals:
        chosen = min(
            range(len(proposals)),
            key=lambda i: (
                proposals[i].parent_region_index,
                proposals[i].local_bbox[1],
                -BBox.model_validate(boxes_payload[i]["local_bbox"]).area,
            ),
        )
        proposals[chosen].attributes["is_scaffold_candidate"] = True
        boxes_payload[chosen]["is_scaffold_candidate"] = True
    used = {n.local_id for n in proposals}
    for previous_node in old:
        if previous_node["local_id"] not in used:
            geometry = previous_node["geometry"]
            proposals.append(
                NodeProposal(
                    local_id=previous_node["local_id"],
                    node_type="molecule",
                    parent_local_id=local,
                    local_bbox=BBox.model_validate(geometry["local_bbox"]).as_tuple(),
                    parent_region_index=geometry["parent_region_index"],
                    attributes=previous_node["attributes"],
                    status="removed",
                )
            )
    return outcome(
        "molecule_boxes",
        {
            "status": "completed" if boxes_payload else "needs_fallback",
            "boxes": boxes_payload,
            "whole_image": whole,
            "image_refs": image_refs,
            "provenance": provenance,
        },
        nodes=proposals,
        review=not boxes_payload,
    )


async def recognize(inputs: dict[str, Any], ctx: SkillContext) -> SkillResult:
    material = target(ctx)
    assert ctx.materials is not None
    single = material.get("type") == "molecule"
    children = (
        [
            {
                "local_id": ctx.target_ref.rsplit("/", 1)[-1],
                "geometry": material["geometry"],
                "attributes": material["attributes"],
                "status": "active",
            }
        ]
        if single
        else [n for n in material.get("children", []) if n["status"] != "removed"]
    )
    images, refs = [], []
    for node in children:
        region_index = 0 if single else node["geometry"]["parent_region_index"]
        image = ctx.materials.images[region_index]
        box = BBox(x0=0, y0=0, x1=1, y1=1) if single else BBox.model_validate(node["geometry"]["local_bbox"])
        png = await asyncio.to_thread(
            crop_masked,
            base64.b64decode(image.data_b64 or ""),
            box,
            [BBox.model_validate(m) for m in node["attributes"].get("masked_regions", [])],
        )
        images.append(base64.b64encode(png).decode())
        refs.append(f"{ctx.document_id}/r{ctx.revision}/{node['local_id']}")
    records: list[dict[str, Any]] = []
    for start in range(0, len(images), 32):
        response = await ctx.tools.call("molparser.recognize", images_b64=images[start : start + 32])
        records.extend({**r, "input_index": r["input_index"] + start} for r in response["results"])
    by_index = {r["input_index"]: r for r in records}
    facts, candidates_payload, updates = [], [], []
    for i, ref in enumerate(refs):
        record = by_index.get(i, {"error": "missing_result"})
        raw = next(
            (
                record[k]
                for k in ("esmi", "caption", "raw_caption")
                if isinstance(record.get(k), str) and record[k].strip()
            ),
            "",
        )
        core, _ = split(raw)
        normalized = await asyncio.to_thread(canonical, core)
        role = infer_from_esmiles(raw)
        candidate = StructureCandidate(
            structure_id=new_id("str"),
            node_ref=ref,
            source="ocsr",
            e_smiles_raw=raw,
            smiles_core=core,
            canonical_smiles=normalized.canonical_smiles,
            inchikey=normalized.inchikey,
            formula=normalized.formula,
            is_markush=role in {"scaffold", "substituent", "sru"},
            confidence=record.get("confidence"),
            validation={
                "valid": normalized.valid and not bool(record.get("error")),
                "error": record.get("error") or normalized.error,
            },
            evidence=[{"node_id": ref, "role": "primary"}],
        ).model_dump()
        facts.append(
            FactProposal(fact_type="structure_candidate", payload=candidate, evidence=candidate["evidence"])
        )
        updates.append(
            {
                "node_ref": ref,
                "attributes": {"molecule_type": role, "structure_id": candidate["structure_id"]},
                "status": "active" if candidate["validation"]["valid"] else "needs_review",
            }
        )
        candidates_payload.append(candidate)
    facts.extend(FactProposal(fact_type="molecule_update", payload=u) for u in updates)
    return outcome(
        "ocsr_result",
        {"structures": candidates_payload},
        facts=facts,
        review=any(not c["validation"]["valid"] for c in candidates_payload),
    )


async def som(inputs: dict[str, Any], ctx: SkillContext) -> SkillResult:
    material = target(ctx)
    assert ctx.materials is not None
    images = []
    for index, image in enumerate(ctx.materials.images):
        boxes = [
            {"local_bbox": n["geometry"]["local_bbox"], "reading_index": n["attributes"]["reading_index"]}
            for n in material.get("children", [])
            if n["status"] != "removed" and n["geometry"]["parent_region_index"] == index
        ]
        data = await asyncio.to_thread(render, base64.b64decode(image.data_b64 or ""), boxes)
        assert ctx.blobs is not None
        images.append({"parent_region_index": index, "blob_ref": await ctx.blobs.put(data, "image/png")})
    return outcome("som_image", {"images": images})


async def label_bind(inputs: dict[str, Any], ctx: SkillContext) -> SkillResult:
    material = target(ctx)
    results = []
    for node in material.get("children", []):
        if node["status"] == "removed":
            continue
        region = material["regions"][node["geometry"]["parent_region_index"]]
        box = local_to_page(
            BBox.model_validate(node["geometry"]["local_bbox"]), BBox.model_validate(region["bbox"])
        )
        results.append(
            {
                "node_ref": f"{ctx.document_id}/r{ctx.revision}/{node['local_id']}",
                "mol_id": node["attributes"]["reading_index"],
                "candidates": candidates(box, material.get("spans", []), region["page"]),
            }
        )
    return outcome("label_candidates", {"candidates": results})


async def mol_backfill(inputs: dict[str, Any], ctx: SkillContext) -> SkillResult:
    material = target(ctx)
    if inputs.get("contract") == "universal":
        from drugpaper.vision.molecular_delivery import image_products

        boxes = [
            {
                "mol_id": n["attributes"]["reading_index"],
                "node_ref": n["ref"],
                "bbox": n["geometry"]["local_bbox"],
                "segment": n["geometry"].get("parent_region_index", 0),
            }
            for n in material.get("children", [])
            if n["status"] != "removed"
        ]
        data = await ctx.tools.call(
            "mol.deliver",
            data=inputs["extraction_payload"],
            boxes=boxes,
            structures=material.get("structures", []),
            blocked_instance_paths=inputs.get("blocked_instance_paths", []),
        )
        references: list[tuple[dict[str, Any], str]] = []

        def collect_references(value: Any, path: str = "$") -> None:
            if isinstance(value, list):
                for i, child in enumerate(value):
                    collect_references(child, f"{path}[{i}]")
            elif isinstance(value, dict):
                label = value.get("reference")
                if (
                    isinstance(label, str)
                    and label.strip()
                    and value.get("mol_id") is None
                    and value.get("scaffold_mol_id") is None
                    and not value.get("compound_series")
                    and not value.get("markush")
                    and (".molecules[" in path or path.endswith(".base_info"))
                ):
                    references.append((value, label))
                for key, child in value.items():
                    if key not in {"molecular_bindings", "molecule_inventory"}:
                        collect_references(child, f"{path}.{key}")

        collect_references(data["payload"])
        source_scope = inputs.get("structure_source_scope", "document")
        data["structure_source_scope"] = source_scope
        if references and source_scope == "current_figure":
            data["errors"].extend(
                {
                    "code": "current_figure_structure_missing",
                    "reference": label,
                    "reason": "Reference text is context only; no complete structure may be imported from another figure.",
                }
                for _, label in references
            )
            data["coverage"]["status"] = "needs_review"
        elif references:
            lookup = await ctx.tools.call(
                "mol.lookup_references", references=sorted({r for _, r in references})
            )
            data["errors"].extend(lookup["errors"])
            for value, label in references:
                if binding := lookup["resolved"].get(label):
                    value["esmiles"] = binding["e_smiles_raw"]
                    value["molecular_structure"] = {
                        k: binding[k] for k in ("e_smiles_raw", "canonical_smiles", "formula")
                    }
                    value["reference_binding"] = binding
            if lookup["errors"]:
                data["coverage"]["status"] = "needs_review"
        data["image_products"] = await image_products(ctx, data)
        delivery_facts: list[FactProposal] = []

        # Claims are projected only for successfully bound entities, never for failed recognition.
        def delivery_claims(value: Any, path: str) -> None:
            if isinstance(value, list):
                for i, child in enumerate(value):
                    delivery_claims(child, f"{path}[{i}]")
            elif isinstance(value, dict):
                label = value.get("reference") or value.get("label") or value.get("compound")
                for binding in value.get("molecular_bindings", []):
                    primary = binding["field_path"] == path + ".mol_id"
                    single_plural = (
                        value.get("mol_id") is None
                        and isinstance(value.get("mol_ids"), list)
                        and len(value["mol_ids"]) == 1
                        and binding["field_path"] == path + ".mol_ids[0]"
                    )
                    # A compound label must never become a label on its R-group fragments.
                    if label and (primary or single_plural) and binding.get("structure_id"):
                        payload = {
                            "label": str(label),
                            "structure_id": binding["structure_id"],
                            "node_ref": binding["node_ref"],
                            "field_path": path,
                            "evidence": [{"node_id": ctx.target_ref, "role": "primary", "field_path": path}],
                        }
                        delivery_facts.append(
                            FactProposal(
                                fact_type="label_claim", payload=payload, evidence=payload["evidence"]
                            )
                        )
                for key, child in value.items():
                    if key != "molecular_bindings":
                        delivery_claims(child, f"{path}.{key}")

        delivery_claims(data["payload"], "$")

        def persist_instances(value: Any) -> None:
            if isinstance(value, list):
                for child in value:
                    persist_instances(child)
            elif isinstance(value, dict):
                if value.get("assembly_status") == "completed":
                    normalized = value["assembled_molecule"]
                    provenance = value["assembly_provenance"]
                    evidence = [
                        {"node_id": ctx.target_ref, "role": "primary", "field_path": provenance["field_path"]}
                    ]
                    # Persist a full instance separately from its recognized template/fragment nodes.
                    candidate = StructureCandidate(
                        structure_id=new_id("str"),
                        node_ref=ctx.target_ref,
                        source="markush_assembly"
                        if value.get("scaffold_mol_id") is not None
                        or provenance.get("stereochemistry_from_explicit_label")
                        else "ocsr",
                        e_smiles_raw=normalized["canonical_smiles"],
                        smiles_core=normalized["canonical_smiles"],
                        canonical_smiles=normalized["canonical_smiles"],
                        inchikey=normalized["inchikey"],
                        formula=normalized["formula"],
                        validation={"valid": True, "assembly_provenance": provenance},
                        evidence=evidence,
                    ).model_dump()
                    value["assembled_molecule"]["structure_id"] = candidate["structure_id"]
                    delivery_facts.extend(
                        [
                            FactProposal(
                                fact_type="structure_candidate", payload=candidate, evidence=evidence
                            ),
                            FactProposal(
                                fact_type="label_claim",
                                payload={
                                    "label": value["reference"],
                                    "structure_id": candidate["structure_id"],
                                    "node_ref": ctx.target_ref,
                                    "evidence": evidence,
                                },
                                evidence=evidence,
                            ),
                        ]
                    )
                for key, child in value.items():
                    if key not in {"molecular_bindings", "assembly_provenance", "assembled_molecule"}:
                        persist_instances(child)

        persist_instances(data["payload"])
        # Point reaction endpoints at their full, persisted instance, including label-derived stereo.
        instance_index: dict[tuple[str, int], list[dict[str, Any]]] = {}

        def index_instances(value: Any) -> None:
            if isinstance(value, list):
                for child in value:
                    index_instances(child)
            elif isinstance(value, dict):
                if value.get("assembly_status") == "completed" and value.get("mol_id") is not None:
                    instance_index.setdefault((value["reference"], value["mol_id"]), []).append(value)
                for key, child in value.items():
                    if key not in {"assembly_provenance", "assembled_molecule", "molecular_bindings"}:
                        index_instances(child)

        index_instances(data["payload"])

        def project_instances(value: Any) -> None:
            if isinstance(value, list):
                for child in value:
                    project_instances(child)
            elif isinstance(value, dict):
                label = value.get("reference")
                index = value.get("mol_id")
                matches = (
                    instance_index.get((label, index), [])
                    if isinstance(label, str) and isinstance(index, int) and not isinstance(index, bool)
                    else []
                )
                graphs = {i["assembled_molecule"]["canonical_smiles"] for i in matches}
                if len(graphs) == 1 and not value.get("assembly_status"):
                    value["instance_structure"] = matches[0]["assembled_molecule"]
                    value["instance_source_path"] = matches[0]["assembly_provenance"]["field_path"]
                for key, child in list(value.items()):
                    if key not in {
                        "instance_structure",
                        "assembly_provenance",
                        "assembled_molecule",
                        "molecular_bindings",
                    }:
                        project_instances(child)

        project_instances(data["payload"])
        return outcome(
            "mol_backfill",
            data,
            facts=delivery_facts,
            review=bool(data["errors"])
            or data["payload"].get("status") in {"unclear", "needs_review", "failed", "not_a_table"},
        )
    structures = {s["node_ref"]: s for s in material.get("structures", [])}
    molecules, nodes_by_id = {}, {}
    for n in material.get("children", []):
        ref = f"{ctx.document_id}/r{ctx.revision}/{n['local_id']}"
        if n["status"] == "removed" or ref not in structures:
            continue
        i = n["attributes"]["reading_index"]
        s = structures[ref]
        molecules[i] = Molecule(
            ref,
            s.get("e_smiles_raw") or "",
            list(BBox.model_validate(n["geometry"]["local_bbox"]).as_tuple()),
            n["attributes"].get("molecule_type", "unknown"),
        )
        nodes_by_id[i] = s
    original = inputs["extraction_payload"]
    data = backfill(ctx.target_ref, original, molecules, inputs.get("image_type", 10))
    facts: list[FactProposal] = []

    def claims(value: Any, path: str) -> None:
        if isinstance(value, list):
            for i, item in enumerate(value):
                claims(item, f"{path}[{i}]")
        elif isinstance(value, dict):
            ids = value.get("mol_ids", [value.get("mol_id")])
            label = value.get("reference") or value.get("label") or value.get("compound")
            for i in ids if isinstance(ids, list) else []:
                if type(i) is int and i in nodes_by_id and label:
                    s = nodes_by_id[i]
                    payload = {
                        "label": str(label),
                        "structure_id": s["structure_id"],
                        "node_ref": s["node_ref"],
                        "field_path": path,
                        "evidence": [{"node_id": ctx.target_ref, "role": "primary", "field_path": path}],
                    }
                    facts.append(
                        FactProposal(fact_type="label_claim", payload=payload, evidence=payload["evidence"])
                    )
            for key, child in value.items():
                claims(child, f"{path}.{key}")

    claims(original, "$")
    for i, role in data["roles"].items():
        facts.append(
            FactProposal(
                fact_type="molecule_update",
                payload={"node_ref": nodes_by_id[i]["node_ref"], "attributes": {"molecule_type": role}},
            )
        )
    return outcome("mol_backfill", {"original": original, **data}, facts=facts, review=bool(data["errors"]))


async def compound_resolve(inputs: dict[str, Any], ctx: SkillContext) -> SkillResult:
    material = target(ctx)
    existing = [dict(c) for c in material.get("compounds", [])]
    if "current_node_refs" in material:
        current_refs = set(material["current_node_refs"])
        for compound in existing:
            for field_name in ("evidence", "role_evidence"):
                if field_name in compound:
                    compound[field_name] = [
                        e for e in compound[field_name] if e.get("node_id") in current_refs
                    ]
    data = resolve(
        ctx.document_id or ctx.target_ref,
        claims=material.get("label_claims", []),
        structures=material.get("structures", []),
        assignments=material.get("assignments", []),
        text=material.get("text", ""),
        existing=existing,
        scope=inputs.get("scope"),
        mentions=material.get("text_mentions", []),
    )
    facts = [FactProposal(fact_type="compound", payload=c) for c in data["compounds"]]
    facts += [
        FactProposal(fact_type="chemistry_conflict", payload=c, evidence=c["evidence"])
        for c in data["conflicts"]
    ]
    facts += [
        FactProposal(fact_type="structure_binding", payload={"structure_id": key, "compound_id": value})
        for key, value in data["bindings"].items()
    ]
    return outcome(
        "compound_resolve",
        data,
        facts=facts,
        review=bool(data["conflicts"])
        or any(c.get("role_status") == "needs_review" for c in data["compounds"])
        or any(m.get("source_status") == "needs_review" for m in material.get("text_mentions", []))
        or any(
            s.get("source") == "opsin"
            and (
                s.get("validation", {}).get("formula_match") is not True
                or s.get("validation", {}).get("opsin_status") == "warning"
            )
            for s in material.get("structures", [])
        ),
    )


async def markush_assemble(inputs: dict[str, Any], ctx: SkillContext) -> SkillResult:
    if "rows" not in inputs:
        data = await ctx.tools.call("markush.substitute", **inputs)
        return outcome("markush_assemble", data, review=data["status"] != "completed")
    from dataclasses import asdict

    from drugpaper.chem.markush import assemble_row

    scaffold_id = new_id("scaffold")
    evidence = [{"node_id": ctx.target_ref, "role": "primary"}]
    facts = [
        FactProposal(
            fact_type="markush_scaffold",
            payload={
                "scaffold_id": scaffold_id,
                "e_smiles_raw": inputs["caption"],
                "variable_defs": inputs.get("variable_defs", []),
                "source_node_id": ctx.target_ref,
                "evidence": evidence,
            },
            evidence=evidence,
        )
    ]
    rows = []
    for i, row in enumerate(inputs["rows"]):
        row_evidence = [{"node_id": ctx.target_ref, "role": "primary", "field_path": f"rows[{i}]"}]
        assembled = await asyncio.to_thread(assemble_row, inputs["caption"], row["values"])
        record = {"label": row["label"], **asdict(assembled)}
        assignment = {
            "assignment_id": new_id("rgroup"),
            "scaffold_id": scaffold_id,
            "label": row["label"],
            "values": row["values"],
            "evidence": row_evidence,
        }
        if assembled.status == "completed":
            normalized = canonical(assembled.products[0])
            candidate = StructureCandidate(
                structure_id=new_id("str"),
                node_ref=ctx.target_ref,
                source="markush_assembly",
                smiles_core=assembled.products[0],
                canonical_smiles=normalized.canonical_smiles,
                inchikey=normalized.inchikey,
                formula=normalized.formula,
                validation={"valid": normalized.valid},
                evidence=row_evidence,
            ).model_dump()
            assignment["assembled_structure_id"] = candidate["structure_id"]
            facts.extend(
                [
                    FactProposal(fact_type="structure_candidate", payload=candidate, evidence=row_evidence),
                    FactProposal(
                        fact_type="label_claim",
                        payload={
                            "label": row["label"],
                            "structure_id": candidate["structure_id"],
                            "node_ref": ctx.target_ref,
                            "evidence": row_evidence,
                        },
                        evidence=row_evidence,
                    ),
                ]
            )
        facts.append(FactProposal(fact_type="rgroup_assignment", payload=assignment, evidence=row_evidence))
        rows.append(record)
    return outcome(
        "markush_assemble",
        {"status": "completed", "products": [p for r in rows for p in r["products"]], "rows": rows},
        facts=facts,
        review=any(r["status"] != "completed" for r in rows),
    )
