import copy

import pytest

from drugpaper.chem.assembly_compat import normalize_locant_text
from drugpaper.chem.assembly_inputs import preserves_assignment, project_inputs
from drugpaper.facts.molecular_delivery import deliver
from tests.unit.figure.test_assembly_tools import SCAFFOLD
from tests.unit.figure.test_compound_instances import scene


def raw(x="O", y="H", z="F", name="XCHYZ", group="OCH2F"):
    return {
        "substituents": {"X": x, "Y": y, "Z": z, name: group},
        "position_definitions": {"CHO": "4", name: "2′"},
    }


def test_composite_variables_project_without_mutating_source():
    value = raw()
    original = copy.deepcopy(value)
    p = project_inputs(
        ["XCHYZ"], **dict(groups=value["substituents"], positions=value["position_definitions"])
    )
    assert p["substituents"] == {"XCHYZ": "OCH2F"}
    assert p["position_definitions"] == {
        "CHO position": "4",
        "XCHYZ position": "2'",
        "X": "O",
        "Y, Z": "H, F",
    }
    assert p["composite_variables"] == {"X": "O", "Y": "H", "Z": "F"}
    assert value == original


@pytest.mark.parametrize(
    "source,expected",
    [
        ("上方环4位", "4"),
        ("下方prime环2′位", "2'"),
        ("upper ring 5", "5"),
        ("primed ring 3", "3'"),
        ("between 3 and 4", "between 3 and 4"),
    ],
)
def test_closed_position_wrappers_normalize_without_choosing_from_ambiguous_prose(source, expected):
    assert normalize_locant_text(source) == expected


def test_descriptive_locants_assemble_without_rewriting_source_position_evidence():
    boxes, structures = scene(SCAFFOLD)
    value = raw()
    value["position_definitions"] = {"CHO position": "上方环4位", "XCHYZ position": "下方prime环2′位"}
    payload = {
        "compound_instances": [
            {
                "reference": "sample",
                "scaffold_mol_id": 0,
                "attachments": ["XCHYZ"],
                "evidence_text": "Explicit source",
                **value,
            }
        ]
    }
    result = deliver(payload, boxes[:1], structures[:1])
    member = result["payload"]["compound_instances"][0]
    assert result["errors"] == [] and member["assembly_status"] == "completed"
    assert member["position_definitions"] == value["position_definitions"]


def test_partial_symbolic_name_is_an_alias_only_with_proof():
    value = raw("S", "F", "F", "SCHYZ", "SCHF2")
    p = project_inputs(["XCHYZ"], value["substituents"], value["position_definitions"])
    assert p["substituents"] == {"XCHYZ": "SCHF2"}
    assert p["attachment_aliases"] == {"SCHYZ": "XCHYZ"}
    assert p["position_definitions"]["XCHYZ position"] == "2'"


@pytest.mark.parametrize(
    "change",
    [
        {"XCHYZ": "SCHF2"},
        {"Y": "F"},
        {"X": "S"},
    ],
)
def test_conflicting_joint_and_internal_assignments_fail(change):
    value = raw()
    value["substituents"].update(change)
    with pytest.raises(ValueError, match="composite_assignment_conflict"):
        project_inputs(["XCHYZ"], value["substituents"], value["position_definitions"])


def test_unknown_fields_and_open_groups_are_not_silently_removed():
    value = raw()
    value["substituents"]["Unknown"] = "Alk"
    p = project_inputs(["XCHYZ"], value["substituents"], value["position_definitions"])
    assert p["substituents"]["Unknown"] == "Alk"
    assert project_inputs(["R"], {"R": "SOAlk"})["substituents"] == {"R": "SOAlk"}


def test_duplicate_explicit_locant_is_computed_once_without_erasing_source():
    positions = {"site": "3′-Cl"}
    result = project_inputs(["site"], {"site": "3′-Cl"}, positions, ["site"])
    assert result["position_definitions"] == {}
    assert result["source_position_definitions"] == positions
    separate = project_inputs(["site"], {"site": "Cl"}, positions, ["site"])
    assert separate["substituents"] == {"site": "3'-Cl"}
    assert separate["position_definitions"] == {}
    assert separate["source_substituents"] == {"site": "Cl"}
    assert separate["source_position_definitions"] == positions
    conflict = project_inputs(["site"], {"site": "3′-Cl"}, {"site": "3-Cl"}, ["site"])
    assert conflict["position_definitions"] == {"site": "3-Cl"}
    prose = project_inputs(["site"], {"site": "3′-Cl"}, {"site": "perhaps 3′-Cl"}, ["site"])
    assert prose["position_definitions"] == {"site": "perhaps 3'-Cl"}
    assert prose["source_position_definitions"] == {"site": "perhaps 3′-Cl"}


def test_positional_conflict_is_not_normalized_away():
    value = raw()
    value["position_definitions"]["CHO position"] = "3"
    with pytest.raises(ValueError, match="position_alias_conflict"):
        project_inputs(["XCHYZ"], value["substituents"], value["position_definitions"])


def test_equivalent_trial_preserves_reference_semantics_but_position_changes_do_not():
    original = raw()
    p = project_inputs(["XCHYZ"], original["substituents"], original["position_definitions"])
    candidate = {k: p[k] for k in ("substituents", "position_definitions")}
    assert preserves_assignment(["XCHYZ"], original, candidate)
    candidate["position_definitions"]["CHO position"] = "3"
    assert not preserves_assignment(["XCHYZ"], original, candidate)


def test_deterministic_delivery_uses_projection_and_retains_source_and_provenance():
    boxes, structures = scene(SCAFFOLD)
    boxes, structures = boxes[:1], structures[:1]
    value = raw("S", "H", "F", "SCHYZ", "SCH2F")
    instance = {
        "reference": "sample",
        "scaffold_mol_id": 0,
        "evidence_text": "Explicit source assignment",
        **value,
    }
    source = {"compound_instances": [instance]}
    before = copy.deepcopy(source)
    result = deliver(source, boxes, structures)
    member = result["payload"]["compound_instances"][0]
    assert result["errors"] == []
    assert member["assembly_status"] == "completed"
    assert member["assembled_molecule"]["canonical_smiles"] == "O=Cc1ccc(-c2ccccc2SCF)cc1"
    assert member["substituents"] == value["substituents"]
    assert member["assembly_provenance"]["assembly_input_projection"]["attachment_aliases"] == {
        "SCHYZ": "XCHYZ"
    }
    assert source == before


def test_trial_can_normalize_reference_fields_without_bypassing_factual_constraints():
    from drugpaper.agents.figure.assembly_tools import try_assembly
    from drugpaper.agents.figure.reference_assignments import supplement_errors
    from tests.unit.figure.test_assembly_tools import log, materials

    value = raw()
    member = {"reference": "sample", "scaffold_mol_id": 0, "evidence_text": "Source", **value}
    path = "/pathways/0/steps/0/products/0"
    product = {
        "scaffold_mol_id": 0,
        "reference": "sample",
        "variants": [member],
        "markush": {"substitution_cases": [{"expression": "XCHYZ"}]},
    }
    extraction = {"pathways": [{"steps": [{"products": [product]}]}]}
    ledger = log()
    required = {"reference": "sample", **value}
    ledger["reference_assignments"] = [
        {"product_path": path, "scaffold_mol_id": 0, "target_reference": "sample", "assignments": [required]}
    ]
    projected = project_inputs(["XCHYZ"], value["substituents"], value["position_definitions"])
    args = {
        "items": [
            {
                "instance_path": path + "/variants/0",
                "evidence_ids": ["e1"],
                "note": "Equivalent calculation projection; source unchanged",
                "substituents": projected["substituents"],
                "position_definitions": projected["position_definitions"],
            }
        ]
    }
    result = try_assembly(materials(SCAFFOLD), extraction, ledger, args)["results"][0]
    assert result["status"] == "completed"
    normalized = copy.deepcopy(extraction)
    normalized["pathways"][0]["steps"][0]["products"][0]["variants"][0] = result["candidate"]
    assert supplement_errors(normalized, ledger["reference_assignments"]) == []
    args["items"][0]["position_definitions"]["CHO position"] = "3"
    failed = try_assembly(materials(SCAFFOLD), extraction, ledger, args)["results"][0]
    assert failed["status"] == "failed"
    assert "conflicts with registered" in failed["error"]
