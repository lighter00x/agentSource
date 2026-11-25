"""Tests for classification-specific Molecule E-SMILES backfill."""

import json

import pytest

from drugpaper.chem.roles import infer_from_esmiles
from drugpaper.facts.legacy_backfill import (
    annotate_image_molecule_types,
    backfill_image_molecules,
    update_backfill_error_report,
)
from drugpaper.facts.legacy_molecule import Molecule as Record


def Molecule(mid, esmiles, **kwargs):
    return Record(mid, esmiles, kwargs.get("bbox"), infer_from_esmiles(esmiles))


IMAGE_NAME = "image.jpg"


def _molecule_index(count=8):
    return {
        (IMAGE_NAME, index): Molecule(
            mid=f"{IMAGE_NAME}_{index}",
            esmiles=f"ESMI-{index}",
            image_name=IMAGE_NAME,
            index=index,
        )
        for index in range(count)
    }


def _contains_key(value, target):
    if isinstance(value, dict):
        return target in value or any(_contains_key(item, target) for item in value.values())
    if isinstance(value, list):
        return any(_contains_key(item, target) for item in value)
    return False


def _collect_esmiles_values(value):
    found = []
    if isinstance(value, dict):
        for item in value.values():
            found.extend(_collect_esmiles_values(item))
    elif isinstance(value, list):
        for item in value:
            found.extend(_collect_esmiles_values(item))
    elif isinstance(value, str) and value.startswith("ESMI-"):
        found.append(value)
    return found


@pytest.mark.parametrize(
    ("image_type", "data", "expected"),
    [
        (
            1,
            {
                "base_info": {"mol_id": 0, "reference": "1"},
                "optimization_steps": [
                    {
                        "mol_id": 1,
                        "extra": [{"mol_id": 2, "substituents": {"R1": "Cl", "R2": 2}}],
                    }
                ],
            },
            ["ESMI-0", "ESMI-1", "ESMI-2", "ESMI-2"],
        ),
        (
            2,
            {
                "routes": [
                    {
                        "starting_materials": [{"mol_id": 0, "reference": "1"}],
                        "transformations": [
                            {
                                "from": [{"mol_id": 0, "reference": "1"}],
                                "to": {"mol_id": 1, "reference": "2"},
                                "extra": [{"mol_id": 2, "substituents": {"R1": 2}}],
                            }
                        ],
                        "branch_topology": [
                            {
                                "branch_from": {"mol_id": 0, "reference": "1"},
                                "branches_to": [{"mol_id": 1, "reference": "2"}],
                                "possible_branch_points": [],
                            }
                        ],
                    }
                ],
            },
            ["ESMI-0", "ESMI-0", "ESMI-1", "ESMI-2", "ESMI-2", "ESMI-0", "ESMI-1"],
        ),
        (
            3,
            {
                "table_metadata": {"scaffold": {"mol_id": 0}},
                "content": [{"mol_id": 1, "substituents": {"R1": 2}}],
            },
            ["ESMI-0", "ESMI-1", "ESMI-2"],
        ),
        (
            4,
            {
                "molecules": [{"mol_id": 0, "substituents": {"R1": 1}}],
            },
            ["ESMI-0", "ESMI-1"],
        ),
        (
            5,
            {
                "docking_metadata": {"ligand": {"mol_id": 0, "reference": "A"}},
            },
            ["ESMI-0"],
        ),
        (
            6,
            {
                "molecules": [
                    {
                        "mol_id": 0,
                        "warhead": {"mol_id": 1},
                        "e3_ligand": {"mol_id": 2},
                    }
                ],
            },
            ["ESMI-0", "ESMI-1", "ESMI-2"],
        ),
        (
            7,
            {
                "parent_drug": {"mol_id": 0},
                "metabolites": [{"mol_id": 1, "precursor": {"mol_id": 0}}],
            },
            ["ESMI-0", "ESMI-1", "ESMI-0"],
        ),
        (
            8,
            {
                "model_metadata": {"reference_molecules": [{"mol_id": 0}]},
                "overlay_info": [{"mol_id": 1}],
            },
            ["ESMI-0", "ESMI-1"],
        ),
        (
            9,
            {
                "data_points": [{"mol_id": 0, "reference": "Lead"}],
            },
            ["ESMI-0"],
        ),
        (
            10,
            {
                "molecules": [{"mol_id": 0, "reference": "Compound A"}],
            },
            ["ESMI-0"],
        ),
    ],
)
def test_backfill_all_image_schemas(image_type, data, expected):
    errors = backfill_image_molecules(data, image_type, _molecule_index(), IMAGE_NAME)

    assert errors == []
    assert not _contains_key(data, "mol_id")
    assert sorted(_collect_esmiles_values(data)) == sorted(expected)


def test_missing_molecule_keeps_index_and_records_trace():
    data = {"molecules": [{"reference": "Compound X", "mol_id": 99}]}

    errors = backfill_image_molecules(data, 4, _molecule_index(2), IMAGE_NAME)

    assert data["molecules"][0] == {"reference": "Compound X", "mol_id": 99}
    assert errors == [
        {
            "image_name": IMAGE_NAME,
            "image_type": 4,
            "field_path": "molecules[0].mol_id",
            "mol_id": 99,
            "available_mol_ids": [0, 1],
            "reason": "mol_id_not_found",
        }
    ]


def test_null_molecule_id_is_removed_without_error():
    data = {"docking_metadata": {"ligand": {"reference": "Ligand A", "mol_id": None}}}

    errors = backfill_image_molecules(data, 5, _molecule_index(), IMAGE_NAME)

    assert data["docking_metadata"]["ligand"] == {"reference": "Ligand A"}
    assert errors == []


def test_legacy_multi_route_topology_is_converted_to_molecule_objects():
    data = {
        "routes": [
            {
                "transformations": [
                    {
                        "from": [{"mol_id": 0, "reference": "1"}],
                        "to": {"mol_id": 1, "reference": "2"},
                    }
                ],
                "branch_topology": [
                    {
                        "branch_from_mol_id": 0,
                        "branches_to_mol_ids": [1],
                        "possible_branch_points": [],
                    }
                ],
            }
        ],
    }

    errors = backfill_image_molecules(data, 2, _molecule_index(), IMAGE_NAME)
    topology = data["routes"][0]["branch_topology"][0]

    assert errors == []
    assert topology == {
        "branch_from": {"reference": "1", "esmiles": "ESMI-0"},
        "branches_to": [{"reference": "2", "esmiles": "ESMI-1"}],
        "possible_branch_points": [],
    }


def test_multi_route_topology_failure_keeps_reference_and_mol_id():
    data = {
        "routes": [
            {
                "branch_topology": [
                    {
                        "branch_from": {"reference": "12a", "mol_id": 99},
                        "branches_to": [],
                        "possible_branch_points": [],
                    }
                ],
            }
        ],
    }

    errors = backfill_image_molecules(data, 2, _molecule_index(2), IMAGE_NAME)

    assert data["routes"][0]["branch_topology"][0]["branch_from"] == {
        "reference": "12a",
        "mol_id": 99,
    }
    assert errors == [
        {
            "image_name": IMAGE_NAME,
            "image_type": 2,
            "field_path": "routes[0].branch_topology[0].branch_from.mol_id",
            "mol_id": 99,
            "available_mol_ids": [0, 1],
            "reason": "mol_id_not_found",
        }
    ]


def test_detected_bbox_missing_from_output_is_traceable():
    molecule = Molecule(
        mid=f"{IMAGE_NAME}_7",
        esmiles="ESMI-7",
        image_name=IMAGE_NAME,
        index=7,
        bbox=[0.1, 0.2, 0.3, 0.4],
    )

    errors = backfill_image_molecules({}, 10, {(IMAGE_NAME, 7): molecule}, IMAGE_NAME)

    assert errors == [
        {
            "image_name": IMAGE_NAME,
            "image_type": 10,
            "field_path": None,
            "mol_id": 7,
            "molecule_id": f"{IMAGE_NAME}_7",
            "bbox": [0.1, 0.2, 0.3, 0.4],
            "reason": "bbox_not_referenced_in_output",
        }
    ]


def test_referenced_bbox_is_not_reported_as_missing():
    molecule = Molecule(
        mid=f"{IMAGE_NAME}_7",
        esmiles="ESMI-7",
        image_name=IMAGE_NAME,
        index=7,
        bbox=[0.1, 0.2, 0.3, 0.4],
    )
    data = {"molecules": [{"reference": "7a", "mol_id": 7}]}

    errors = backfill_image_molecules(data, 10, {(IMAGE_NAME, 7): molecule}, IMAGE_NAME)

    assert errors == []
    assert data == {"molecules": [{"reference": "7a", "esmiles": "ESMI-7"}]}


def test_error_report_replaces_only_processed_image_entries(tmp_path):
    report_path = tmp_path / "molecule_backfill_errors.json"
    report_path.write_text(
        json.dumps(
            {
                "paper_name": "paper",
                "errors": [
                    {"image_name": "old.jpg", "reason": "old"},
                    {"image_name": "keep.jpg", "reason": "keep"},
                ],
            }
        ),
        encoding="utf-8",
    )
    new_error = {"image_name": "old.jpg", "reason": "new"}

    update_backfill_error_report(report_path, "paper", ["old.jpg"], [new_error])

    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report == {
        "paper_name": "paper",
        "errors": [
            {"image_name": "keep.jpg", "reason": "keep"},
            new_error,
        ],
    }


def test_sar_roles_distinguish_scaffold_substituents_and_full_molecules():
    molecule_index = _molecule_index(4)
    data = {
        "table_metadata": {"scaffold": {"mol_id": 0}},
        "content": [
            {"reference": "1", "mol_id": None, "substituents": {"R": 1}},
            {"reference": "2", "mol_id": 2, "substituents": {"R": 3}},
        ],
    }

    changed = annotate_image_molecule_types(data, 3, molecule_index, IMAGE_NAME)

    assert changed is True
    assert molecule_index[(IMAGE_NAME, 0)].molecule_type == "scaffold"
    assert molecule_index[(IMAGE_NAME, 1)].molecule_type == "substituent"
    assert molecule_index[(IMAGE_NAME, 2)].molecule_type == "full"
    assert molecule_index[(IMAGE_NAME, 3)].molecule_type == "substituent"
    assert all(molecule.molecule_type_source == "image_schema" for molecule in molecule_index.values())


def test_sar_row_mol_id_can_reference_the_same_substituent_bbox():
    molecule_index = _molecule_index(2)
    data = {
        "table_metadata": {"scaffold": {"mol_id": 0}},
        "content": [
            {"reference": "1", "mol_id": 1, "substituents": {"R": 1}},
        ],
    }

    annotate_image_molecule_types(data, 3, molecule_index, IMAGE_NAME)

    assert molecule_index[(IMAGE_NAME, 0)].molecule_type == "scaffold"
    assert molecule_index[(IMAGE_NAME, 1)].molecule_type == "substituent"
    assert molecule_index[(IMAGE_NAME, 1)].molecule_type_source == "image_schema"


@pytest.mark.parametrize("image_type", [1, 2])
def test_route_sar_extra_mol_id_can_reference_the_same_substituent_bbox(image_type):
    molecule_index = _molecule_index(3)
    extra = {"reference": "5a", "mol_id": 2, "substituents": {"R": 2}}
    if image_type == 1:
        data = {"optimization_steps": [{"mol_id": 0, "is_sar_template": True, "extra": [extra]}]}
    else:
        data = {
            "routes": [
                {
                    "transformations": [
                        {
                            "from": [],
                            "to": {"mol_id": 0},
                            "is_sar_template": True,
                            "extra": [extra],
                        }
                    ]
                }
            ]
        }

    annotate_image_molecule_types(data, image_type, molecule_index, IMAGE_NAME)
    errors = backfill_image_molecules(data, image_type, molecule_index, IMAGE_NAME)

    assert errors == []
    assert molecule_index[(IMAGE_NAME, 0)].molecule_type == "scaffold"
    assert molecule_index[(IMAGE_NAME, 2)].molecule_type == "substituent"
    assert extra == {
        "reference": "5a",
        "esmiles": "ESMI-2",
        "substituents": {"R": "ESMI-2"},
    }


def test_generic_full_schema_role_does_not_override_esmiles_substituent_type():
    molecule = Molecule(
        mid=f"{IMAGE_NAME}_0",
        esmiles="*CC<sep>",
        image_name=IMAGE_NAME,
        index=0,
    )
    molecule_index = {(IMAGE_NAME, 0): molecule}

    annotate_image_molecule_types(
        {"data_points": [{"reference": "fragment A", "mol_id": 0}]},
        9,
        molecule_index,
        IMAGE_NAME,
    )

    assert molecule.molecule_type == "substituent"
    assert molecule.molecule_type_source == "esmiles"
