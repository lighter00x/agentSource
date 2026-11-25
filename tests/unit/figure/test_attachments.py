import copy

import pytest
from pydantic import ValidationError

from drugpaper.agents.figure.structure_review import attachment_selection_gaps
from drugpaper.chem.assembly_inputs import project_inputs
from drugpaper.facts.compound_instances import CompoundInstance
from drugpaper.facts.molecular_delivery import deliver
from tests.unit.figure.test_assembly_tools import SCAFFOLD
from tests.unit.figure.test_compound_instances import scene


def member():
    return {
        "reference": "sample",
        "scaffold_mol_id": 0,
        "substituents": {"X": "O", "Y": "H", "Z": "F", "XCHYZ": "OCH2F"},
        "attachments": ["XCHYZ"],
        "evidence_text": "Explicit source choice",
        "position_definitions": {"CHO position": "4", "XCHYZ position": "2′"},
    }


def test_selector_uses_only_selected_values_and_keeps_source_fields():
    value = member()
    before = copy.deepcopy(value)
    boxes, structures = scene(SCAFFOLD)
    out = deliver({"compound_instances": [value]}, boxes[:1], structures[:1])
    m = out["payload"]["compound_instances"][0]
    assert not out["errors"]
    assert m["assembly_status"] == "completed"
    assert m["assembled_molecule"]["canonical_smiles"] == "O=Cc1ccc(-c2ccccc2OCF)cc1"
    assert m["substituents"] == value["substituents"]
    assert m["attachments"] == ["XCHYZ"]
    p = m["assembly_provenance"]["assembly_input_projection"]
    assert p["selection_mode"] == "explicit_attachments"
    assert p["substituents"] == {"XCHYZ": "OCH2F"}
    assert p["explanatory_substituents"] == {"X": "O", "Y": "H", "Z": "F"}
    assert value == before


def test_unselected_integer_is_not_bound_as_a_molecule_but_selected_fragment_is():
    boxes, structures = scene()
    value = {
        "reference": "sample",
        "scaffold_mol_id": 0,
        "substituents": {"R": 1, "explanatory_index": 999},
        "attachments": ["R"],
        "evidence_text": "Selected current fragment",
    }
    out = deliver({"compound_instances": [value]}, boxes, structures)
    assert out["errors"] == []
    m = out["payload"]["compound_instances"][0]
    assert m["assembly_status"] == "completed"
    assert m["backfilled_substituents"] == {"R": "*C"}
    assert m["substituents"]["explanatory_index"] == 999


@pytest.mark.parametrize("selection", [[], ["XCHYZ", "XCHYZ"], ["Unknown"], [""], [1]])
def test_invalid_selector_rejected_by_instance_model(selection):
    value = member()
    value["attachments"] = selection
    with pytest.raises(ValidationError):
        CompoundInstance.model_validate(value)


def test_selected_metadata_or_partial_label_is_not_guessed_into_an_actual_site():
    for selection in [["X"], ["SCHYZ"]]:
        value = member()
        value["substituents"]["SCHYZ"] = "SCH2F"
        value["attachments"] = selection
        boxes, structures = scene(SCAFFOLD)
        out = deliver({"compound_instances": [value]}, boxes[:1], structures[:1])
        assert out["payload"]["compound_instances"][0]["assembly_status"] == "needs_review"
        assert out["errors"][0]["reason"] == "invalid_attachment_selection"


def test_multiple_sites_are_selected_and_missing_sites_fail():
    p = project_inputs(
        ["R", "X", "Y"], {"R": "SO(NH)Me", "X": "H", "Y": "CH", "note": "kept"}, attachments=["R", "X", "Y"]
    )
    assert p["substituents"] == {"R": "SO(NH)Me", "X": "H", "Y": "CH"}
    with pytest.raises(ValueError, match="incomplete_attachment_selection"):
        project_inputs(["R", "X"], {"R": "H", "X": "Cl"}, attachments=["R"])


def test_structure_gate_requires_missing_selector_and_checks_actual_sites():
    value = member()
    inventory = {"structures": [{"mol_id": 0, "attachment_labels": ["XCHYZ"]}]}
    assert attachment_selection_gaps({"compound_instances": [value]}, inventory) == []
    value.pop("attachments")
    issues = attachment_selection_gaps({"compound_instances": [value]}, inventory)
    assert issues[0]["field_path"] == "/compound_instances/0"
    value["attachments"] = ["X", "Y", "Z"]
    assert attachment_selection_gaps({"compound_instances": [value]}, inventory)
    value["attachments"] = ["XCHYZ"]
    assert attachment_selection_gaps({"compound_instances": [value]}, inventory) == []
    value["attachments"] = ["missing"]
    assert attachment_selection_gaps({"compound_instances": [value]}, inventory)


def test_legacy_read_is_explicitly_labelled_and_not_used_when_selector_is_present():
    assert project_inputs(["R"], {"R": "Cl"})["selection_mode"] == "legacy_projection"
    assert project_inputs(["R"], {"R": "Cl"}, attachments=["R"])["selection_mode"] == "explicit_attachments"


def test_reference_supplement_carries_selector_and_prevents_it_being_changed():
    from drugpaper.agents.figure.reference_assignments import (
        apply_reference_assignments,
        record_reference_assignments,
        supplement_errors,
    )
    from tests.unit.figure.test_reference_assignments import args, source, state

    s = state()
    request = args()
    request["assignments"][0]["attachments"] = ["R"]
    record_reference_assignments(source(), s, request)
    updated = apply_reference_assignments(s["extraction"], s["exploration"])
    product = updated["pathways"][0]["steps"][0]["products"][0]
    assert product["variants"][0]["attachments"] == ["R"]
    assert not supplement_errors(updated, s["exploration"]["reference_assignments"])
    product["variants"][0]["substituents"]["other"] = "Cl"
    product["variants"][0]["attachments"] = ["other"]
    assert any(
        e["code"] == "reference_attachment_selection_not_preserved"
        for e in supplement_errors(updated, s["exploration"]["reference_assignments"])
    )


def test_trial_accepts_explicit_selection_and_does_not_mutate_extraction():
    from drugpaper.agents.figure.assembly_tools import try_assembly
    from tests.unit.figure.test_assembly_tools import log, materials

    value = member()
    value.pop("attachments")
    extraction = {"compound_instances": [value]}
    before = copy.deepcopy(extraction)
    out = try_assembly(
        materials(SCAFFOLD),
        extraction,
        log(),
        {
            "items": [
                {
                    "instance_path": "/compound_instances/0",
                    "attachments": ["XCHYZ"],
                    "evidence_ids": ["e1"],
                    "note": "Select the actual combined site from current source",
                }
            ]
        },
    )
    assert out["results"][0]["status"] == "completed"
    assert out["results"][0]["candidate"]["attachments"] == ["XCHYZ"]
    assert extraction == before


async def test_host_blocks_missing_selector_even_when_visual_reviewer_passes(figure_env):
    from types import MethodType, SimpleNamespace

    from drugpaper.agents.figure.graph import FigureAgent
    from drugpaper.agents.figure.structure_review import review_structure
    from tests.unit.figure.test_exploration import harness
    from tests.unit.figure.test_structure_review import audit_payload

    agent, s, _ = await harness(figure_env, [])
    value = member()
    value.pop("attachments")
    s.update(
        chain=["scheme_extract"],
        extraction={"status": "ok", "compound_instances": [value]},
        exploration={"extraction_version": 1, "evidence": [], "gaps": []},
    )

    async def skill(self, state, name, inputs, **kwargs):
        assert name == "structure_audit"
        return audit_payload()

    agent._run_skill = MethodType(skill, agent)
    audit = await review_structure(agent, s)
    assert audit["status"] == "needs_revision"
    assert any(i["issue_id"].startswith("attachment-selection-") for i in audit["issues"])
    await FigureAgent._post(SimpleNamespace(), s)
    assert s["material"]["backfill_errors"][0]["code"] == "structure_review_blocked"
