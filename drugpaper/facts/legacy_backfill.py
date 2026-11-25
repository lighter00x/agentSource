"""Schema-aware E-SMILES backfill for the ten image-understanding outputs."""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping, MutableMapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from drugpaper.facts.legacy_molecule import Molecule, MoleculeType

MoleculeIndex = Mapping[tuple[str, int], Molecule]
BackfillError = dict[str, Any]
BackfillHandler = Callable[[Any, "BackfillContext"], None]


@dataclass
class BackfillContext:
    molecule_index: MoleculeIndex
    image_name: str
    image_type: int
    errors: list[BackfillError] = field(default_factory=list)
    referenced_mol_ids: set[int] = field(default_factory=set)

    @property
    def available_mol_ids(self) -> list[int]:
        return sorted(
            index for (indexed_image, index) in self.molecule_index if indexed_image == self.image_name
        )

    def resolve(self, mol_id: Any, path: str) -> str | None:
        if isinstance(mol_id, bool) or not isinstance(mol_id, int):
            self.record_error(path, mol_id, "invalid_mol_id")
            return None

        self.referenced_mol_ids.add(mol_id)
        molecule = self.molecule_index.get((self.image_name, mol_id))
        if molecule is None:
            self.record_error(path, mol_id, "mol_id_not_found")
            return None
        if not molecule.esmiles or not molecule.esmiles.strip():
            self.record_error(path, mol_id, "empty_esmiles")
            return None
        return molecule.esmiles

    def record_error(self, path: str, mol_id: Any, reason: str) -> None:
        self.errors.append(
            {
                "image_name": self.image_name,
                "image_type": self.image_type,
                "field_path": path,
                "mol_id": mol_id,
                "available_mol_ids": self.available_mol_ids,
                "reason": reason,
            }
        )

    def record_unreferenced_molecules(self) -> None:
        """Trace detected bboxes that the structured output did not bind."""
        for (indexed_image, mol_id), molecule in sorted(
            self.molecule_index.items(), key=lambda item: (item[0][0], item[0][1])
        ):
            if indexed_image != self.image_name or mol_id in self.referenced_mol_ids or molecule.bbox is None:
                continue
            self.errors.append(
                {
                    "image_name": self.image_name,
                    "image_type": self.image_type,
                    "field_path": None,
                    "mol_id": mol_id,
                    "molecule_id": molecule.id,
                    "bbox": molecule.bbox,
                    "reason": "bbox_not_referenced_in_output",
                }
            )


def _dict(value: Any) -> MutableMapping[str, Any] | None:
    return value if isinstance(value, MutableMapping) else None


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _backfill_entity(value: Any, ctx: BackfillContext, path: str) -> None:
    entity = _dict(value)
    if entity is None or "mol_id" not in entity:
        return

    mol_id = entity.get("mol_id")
    if mol_id is None:
        entity.pop("mol_id", None)
        return

    esmiles = ctx.resolve(mol_id, f"{path}.mol_id")
    if esmiles is None:
        return
    entity.pop("mol_id", None)
    entity["esmiles"] = esmiles


def _backfill_substituents(value: Any, ctx: BackfillContext, path: str) -> None:
    substituents = _dict(value)
    if substituents is None:
        return
    for name, mol_id in list(substituents.items()):
        if isinstance(mol_id, bool) or not isinstance(mol_id, int):
            continue
        esmiles = ctx.resolve(mol_id, f"{path}.{name}")
        if esmiles is not None:
            substituents[name] = esmiles


def _backfill_entities(values: Any, ctx: BackfillContext, path: str) -> None:
    for index, value in enumerate(_list(values)):
        _backfill_entity(value, ctx, f"{path}[{index}]")


def backfill_single_route(data: Any, ctx: BackfillContext) -> None:
    root = _dict(data)
    if root is None:
        return
    _backfill_entity(root.get("base_info"), ctx, "base_info")
    for index, step in enumerate(_list(root.get("optimization_steps"))):
        step_obj = _dict(step)
        if step_obj is None:
            continue
        path = f"optimization_steps[{index}]"
        _backfill_entity(step_obj, ctx, path)
        for extra_index, extra in enumerate(_list(step_obj.get("extra"))):
            extra_obj = _dict(extra)
            if extra_obj is not None:
                extra_path = f"{path}.extra[{extra_index}]"
                _backfill_entity(extra_obj, ctx, extra_path)
                _backfill_substituents(
                    extra_obj.get("substituents"),
                    ctx,
                    f"{extra_path}.substituents",
                )


def _collect_route_references(route: MutableMapping[str, Any]) -> dict[int, str]:
    references: dict[int, str] = {}
    for transformation in _list(route.get("transformations")):
        transformation_obj = _dict(transformation)
        if transformation_obj is None:
            continue
        entities = _list(transformation_obj.get("from")) + [transformation_obj.get("to")]
        for entity in entities:
            entity_obj = _dict(entity)
            if entity_obj is None:
                continue
            mol_id = entity_obj.get("mol_id")
            reference = entity_obj.get("reference")
            if isinstance(mol_id, int) and not isinstance(mol_id, bool) and isinstance(reference, str):
                references[mol_id] = reference
    return references


def _topology_entity(
    mol_id: Any,
    reference: Any,
    references: Mapping[int, str],
    ctx: BackfillContext,
    path: str,
) -> dict[str, Any] | None:
    if mol_id is None and reference is None:
        return None
    entity: dict[str, Any] = {}
    if isinstance(mol_id, int) and not isinstance(mol_id, bool):
        entity["mol_id"] = mol_id
        if reference is None:
            reference = references.get(mol_id)
            if reference is None:
                ctx.record_error(path, mol_id, "topology_reference_not_found")
    if reference is not None:
        entity["reference"] = reference
    return entity


def _normalize_topology(
    topology: MutableMapping[str, Any],
    references: Mapping[int, str],
    ctx: BackfillContext,
    path: str,
) -> None:
    """Normalize legacy parallel topology fields to molecule objects."""
    reference_ids = {reference: mol_id for mol_id, reference in references.items()}

    if "branch_from" not in topology:
        mol_id = topology.pop("branch_from_mol_id", None)
        reference = topology.pop("branch_from_reference", None)
        if mol_id is None and reference in reference_ids:
            mol_id = reference_ids[reference]
        topology["branch_from"] = _topology_entity(mol_id, reference, references, ctx, f"{path}.branch_from")

    def normalize_list(
        field: str,
        legacy_id_field: str,
        legacy_reference_field: str,
    ) -> None:
        if field in topology and all(isinstance(item, MutableMapping) for item in _list(topology.get(field))):
            return

        raw_ids = topology.pop(legacy_id_field, None)
        raw_references = topology.pop(legacy_reference_field, None)
        raw_values = topology.get(field)
        if raw_ids is None and isinstance(raw_values, list):
            if all(isinstance(item, int) and not isinstance(item, bool) for item in raw_values):
                raw_ids = raw_values
            elif all(isinstance(item, str) for item in raw_values):
                raw_references = raw_values

        ids = raw_ids if isinstance(raw_ids, list) else []
        refs = raw_references if isinstance(raw_references, list) else []
        length = max(len(ids), len(refs))
        entities = []
        for index in range(length):
            mol_id = ids[index] if index < len(ids) else None
            reference = refs[index] if index < len(refs) else None
            if mol_id is None and reference in reference_ids:
                mol_id = reference_ids[reference]
            entity = _topology_entity(mol_id, reference, references, ctx, f"{path}.{field}[{index}]")
            if entity is not None:
                entities.append(entity)
        topology[field] = entities

    normalize_list("branches_to", "branches_to_mol_ids", "branches_to_references")
    normalize_list(
        "possible_branch_points",
        "possible_branch_point_mol_ids",
        "possible_branch_references",
    )


def backfill_multi_route(data: Any, ctx: BackfillContext) -> None:
    root = _dict(data)
    if root is None:
        return
    for route_index, route in enumerate(_list(root.get("routes"))):
        route_obj = _dict(route)
        if route_obj is None:
            continue
        route_path = f"routes[{route_index}]"
        references = _collect_route_references(route_obj)

        starting_materials = _list(route_obj.get("starting_materials"))
        for material_index, material in enumerate(list(starting_materials)):
            material_path = f"{route_path}.starting_materials[{material_index}]"
            if isinstance(material, MutableMapping):
                _backfill_entity(material, ctx, material_path)
            elif isinstance(material, int) and not isinstance(material, bool):
                esmiles = ctx.resolve(material, material_path)
                if esmiles is not None:
                    starting_materials[material_index] = esmiles

        for transformation_index, transformation in enumerate(_list(route_obj.get("transformations"))):
            transformation_obj = _dict(transformation)
            if transformation_obj is None:
                continue
            transformation_path = f"{route_path}.transformations[{transformation_index}]"
            _backfill_entities(transformation_obj.get("from"), ctx, f"{transformation_path}.from")
            _backfill_entity(transformation_obj.get("to"), ctx, f"{transformation_path}.to")
            for extra_index, extra in enumerate(_list(transformation_obj.get("extra"))):
                extra_obj = _dict(extra)
                if extra_obj is not None:
                    extra_path = f"{transformation_path}.extra[{extra_index}]"
                    _backfill_entity(extra_obj, ctx, extra_path)
                    _backfill_substituents(
                        extra_obj.get("substituents"),
                        ctx,
                        f"{extra_path}.substituents",
                    )

        for topology_index, topology in enumerate(_list(route_obj.get("branch_topology"))):
            topology_obj = _dict(topology)
            if topology_obj is None:
                continue
            topology_path = f"{route_path}.branch_topology[{topology_index}]"
            _normalize_topology(topology_obj, references, ctx, topology_path)
            _backfill_entity(topology_obj.get("branch_from"), ctx, f"{topology_path}.branch_from")
            _backfill_entities(topology_obj.get("branches_to"), ctx, f"{topology_path}.branches_to")
            _backfill_entities(
                topology_obj.get("possible_branch_points"),
                ctx,
                f"{topology_path}.possible_branch_points",
            )


def backfill_sar_table(data: Any, ctx: BackfillContext) -> None:
    root = _dict(data)
    if root is None:
        return
    metadata = _dict(root.get("table_metadata"))
    if metadata is not None:
        _backfill_entity(metadata.get("scaffold"), ctx, "table_metadata.scaffold")
    for index, row in enumerate(_list(root.get("content"))):
        row_obj = _dict(row)
        if row_obj is None:
            continue
        path = f"content[{index}]"
        _backfill_entity(row_obj, ctx, path)
        _backfill_substituents(row_obj.get("substituents"), ctx, f"{path}.substituents")


def backfill_reported_structures(data: Any, ctx: BackfillContext) -> None:
    root = _dict(data)
    if root is None:
        return
    for index, molecule in enumerate(_list(root.get("molecules"))):
        molecule_obj = _dict(molecule)
        if molecule_obj is None:
            continue
        path = f"molecules[{index}]"
        _backfill_entity(molecule_obj, ctx, path)
        _backfill_substituents(molecule_obj.get("substituents"), ctx, f"{path}.substituents")


def backfill_docking(data: Any, ctx: BackfillContext) -> None:
    root = _dict(data)
    metadata = _dict(root.get("docking_metadata")) if root is not None else None
    if metadata is not None:
        _backfill_entity(metadata.get("ligand"), ctx, "docking_metadata.ligand")


def backfill_protac(data: Any, ctx: BackfillContext) -> None:
    root = _dict(data)
    if root is None:
        return
    for index, molecule in enumerate(_list(root.get("molecules"))):
        molecule_obj = _dict(molecule)
        if molecule_obj is None:
            continue
        path = f"molecules[{index}]"
        _backfill_entity(molecule_obj, ctx, path)
        _backfill_entity(molecule_obj.get("warhead"), ctx, f"{path}.warhead")
        _backfill_entity(molecule_obj.get("e3_ligand"), ctx, f"{path}.e3_ligand")


def backfill_metabolic_pathway(data: Any, ctx: BackfillContext) -> None:
    root = _dict(data)
    if root is None:
        return
    _backfill_entity(root.get("parent_drug"), ctx, "parent_drug")
    for index, metabolite in enumerate(_list(root.get("metabolites"))):
        metabolite_obj = _dict(metabolite)
        if metabolite_obj is None:
            continue
        path = f"metabolites[{index}]"
        _backfill_entity(metabolite_obj, ctx, path)
        _backfill_entity(metabolite_obj.get("precursor"), ctx, f"{path}.precursor")


def backfill_pharmacophore(data: Any, ctx: BackfillContext) -> None:
    root = _dict(data)
    if root is None:
        return
    metadata = _dict(root.get("model_metadata"))
    if metadata is not None:
        _backfill_entities(
            metadata.get("reference_molecules"),
            ctx,
            "model_metadata.reference_molecules",
        )
    _backfill_entities(root.get("overlay_info"), ctx, "overlay_info")


def backfill_property_plot(data: Any, ctx: BackfillContext) -> None:
    root = _dict(data)
    if root is not None:
        _backfill_entities(root.get("data_points"), ctx, "data_points")


def backfill_common_image(data: Any, ctx: BackfillContext) -> None:
    root = _dict(data)
    if root is not None:
        _backfill_entities(root.get("molecules"), ctx, "molecules")


def _assign_molecule_role(
    molecule_index: MoleculeIndex,
    image_name: str,
    mol_id: Any,
    role: MoleculeType,
) -> bool:
    if isinstance(mol_id, bool) or not isinstance(mol_id, int):
        return False
    molecule = molecule_index.get((image_name, mol_id))
    if molecule is None:
        return False

    # E-SMILES 明确识别出的局部/模板语法强于 schema 中泛化的 "full entity" 位置。
    if role == MoleculeType.FULL and molecule.molecule_type in {
        MoleculeType.SCAFFOLD.value,
        MoleculeType.SUBSTITUENT.value,
        MoleculeType.SRU.value,
    }:
        return False
    # A specialized schema role must not be erased by duplicate topology references.
    if (
        role == MoleculeType.FULL
        and molecule.molecule_type_source == "image_schema"
        and (molecule.molecule_type != MoleculeType.FULL.value)
    ):
        return False
    return molecule.set_molecule_type(role, "image_schema")


def _assign_entity_role(
    value: Any,
    role: MoleculeType,
    molecule_index: MoleculeIndex,
    image_name: str,
) -> bool:
    entity = _dict(value)
    return entity is not None and _assign_molecule_role(
        molecule_index, image_name, entity.get("mol_id"), role
    )


def _assign_entities_role(
    values: Any,
    role: MoleculeType,
    molecule_index: MoleculeIndex,
    image_name: str,
) -> bool:
    changed = False
    for value in _list(values):
        changed |= _assign_entity_role(value, role, molecule_index, image_name)
    return changed


def _assign_substituent_roles(
    value: Any,
    molecule_index: MoleculeIndex,
    image_name: str,
) -> bool:
    substituents = _dict(value)
    if substituents is None:
        return False
    changed = False
    for mol_id in substituents.values():
        changed |= _assign_molecule_role(molecule_index, image_name, mol_id, MoleculeType.SUBSTITUENT)
    return changed


def annotate_image_molecule_types(
    data: Any,
    image_type: int,
    molecule_index: MoleculeIndex,
    image_name: str,
) -> bool:
    """根据分类 schema 中的字段角色标记 bbox 对应 Molecule 类型。"""
    root = _dict(data)
    if root is None:
        return False
    changed = False

    if image_type == 1:
        changed |= _assign_entity_role(root.get("base_info"), MoleculeType.FULL, molecule_index, image_name)
        for step in _list(root.get("optimization_steps")):
            step_obj = _dict(step)
            if step_obj is None:
                continue
            role = MoleculeType.SCAFFOLD if step_obj.get("is_sar_template") is True else MoleculeType.FULL
            changed |= _assign_entity_role(step_obj, role, molecule_index, image_name)
            for extra in _list(step_obj.get("extra")):
                extra_obj = _dict(extra)
                if extra_obj is not None:
                    changed |= _assign_entity_role(extra_obj, MoleculeType.FULL, molecule_index, image_name)
                    changed |= _assign_substituent_roles(
                        extra_obj.get("substituents"), molecule_index, image_name
                    )

    elif image_type == 2:
        for route in _list(root.get("routes")):
            route_obj = _dict(route)
            if route_obj is None:
                continue
            changed |= _assign_entities_role(
                route_obj.get("starting_materials"),
                MoleculeType.FULL,
                molecule_index,
                image_name,
            )
            for transformation in _list(route_obj.get("transformations")):
                transformation_obj = _dict(transformation)
                if transformation_obj is None:
                    continue
                changed |= _assign_entities_role(
                    transformation_obj.get("from"),
                    MoleculeType.FULL,
                    molecule_index,
                    image_name,
                )
                role = (
                    MoleculeType.SCAFFOLD
                    if transformation_obj.get("is_sar_template") is True
                    else MoleculeType.FULL
                )
                changed |= _assign_entity_role(transformation_obj.get("to"), role, molecule_index, image_name)
                for extra in _list(transformation_obj.get("extra")):
                    extra_obj = _dict(extra)
                    if extra_obj is not None:
                        changed |= _assign_entity_role(
                            extra_obj, MoleculeType.FULL, molecule_index, image_name
                        )
                        changed |= _assign_substituent_roles(
                            extra_obj.get("substituents"), molecule_index, image_name
                        )

    elif image_type == 3:
        metadata = _dict(root.get("table_metadata"))
        if metadata is not None:
            changed |= _assign_entity_role(
                metadata.get("scaffold"),
                MoleculeType.SCAFFOLD,
                molecule_index,
                image_name,
            )
        for row in _list(root.get("content")):
            row_obj = _dict(row)
            if row_obj is None:
                continue
            changed |= _assign_entity_role(row_obj, MoleculeType.FULL, molecule_index, image_name)
            changed |= _assign_substituent_roles(row_obj.get("substituents"), molecule_index, image_name)

    elif image_type == 4:
        for molecule in _list(root.get("molecules")):
            molecule_obj = _dict(molecule)
            if molecule_obj is None:
                continue
            role = MoleculeType.SCAFFOLD if _dict(molecule_obj.get("substituents")) else MoleculeType.FULL
            changed |= _assign_entity_role(molecule_obj, role, molecule_index, image_name)
            changed |= _assign_substituent_roles(molecule_obj.get("substituents"), molecule_index, image_name)

    elif image_type == 5:
        metadata = _dict(root.get("docking_metadata"))
        if metadata is not None:
            changed |= _assign_entity_role(
                metadata.get("ligand"), MoleculeType.FULL, molecule_index, image_name
            )

    elif image_type == 6:
        for molecule in _list(root.get("molecules")):
            molecule_obj = _dict(molecule)
            if molecule_obj is None:
                continue
            changed |= _assign_entity_role(molecule_obj, MoleculeType.FULL, molecule_index, image_name)
            changed |= _assign_entity_role(
                molecule_obj.get("warhead"),
                MoleculeType.COMPONENT,
                molecule_index,
                image_name,
            )
            changed |= _assign_entity_role(
                molecule_obj.get("e3_ligand"),
                MoleculeType.COMPONENT,
                molecule_index,
                image_name,
            )

    elif image_type == 7:
        changed |= _assign_entity_role(root.get("parent_drug"), MoleculeType.FULL, molecule_index, image_name)
        for metabolite in _list(root.get("metabolites")):
            metabolite_obj = _dict(metabolite)
            if metabolite_obj is not None:
                changed |= _assign_entity_role(metabolite_obj, MoleculeType.FULL, molecule_index, image_name)

    elif image_type == 8:
        metadata = _dict(root.get("model_metadata"))
        if metadata is not None:
            changed |= _assign_entities_role(
                metadata.get("reference_molecules"),
                MoleculeType.FULL,
                molecule_index,
                image_name,
            )
        changed |= _assign_entities_role(
            root.get("overlay_info"), MoleculeType.FULL, molecule_index, image_name
        )

    elif image_type == 9:
        changed |= _assign_entities_role(
            root.get("data_points"), MoleculeType.FULL, molecule_index, image_name
        )

    else:
        changed |= _assign_entities_role(root.get("molecules"), MoleculeType.FULL, molecule_index, image_name)

    return changed


BACKFILL_HANDLERS: dict[int, BackfillHandler] = {
    1: backfill_single_route,
    2: backfill_multi_route,
    3: backfill_sar_table,
    4: backfill_reported_structures,
    5: backfill_docking,
    6: backfill_protac,
    7: backfill_metabolic_pathway,
    8: backfill_pharmacophore,
    9: backfill_property_plot,
    10: backfill_common_image,
}


def backfill_image_molecules(
    data: Any,
    image_type: int,
    molecule_index: MoleculeIndex,
    image_name: str,
) -> list[BackfillError]:
    """Backfill one parsed image JSON using its classification-specific schema."""
    ctx = BackfillContext(molecule_index, image_name, image_type)
    handler = BACKFILL_HANDLERS.get(image_type, backfill_reported_structures)
    handler(data, ctx)
    ctx.record_unreferenced_molecules()
    return ctx.errors


def update_backfill_error_report(
    report_path: Path,
    paper_name: str,
    processed_images: list[str],
    new_errors: list[BackfillError],
) -> None:
    """Merge current-image errors into the paper-level trace report."""
    existing_errors: list[BackfillError] = []
    if report_path.is_file():
        try:
            existing = json.loads(report_path.read_text(encoding="utf-8"))
            if isinstance(existing, dict) and isinstance(existing.get("errors"), list):
                existing_errors = existing["errors"]
        except (OSError, json.JSONDecodeError):
            existing_errors = []

    processed = set(processed_images)
    retained_errors = [
        error
        for error in existing_errors
        if isinstance(error, dict) and error.get("image_name") not in processed
    ]
    report_path.write_text(
        json.dumps(
            {
                "paper_name": paper_name,
                "errors": retained_errors + new_errors,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
