import pytest
from pydantic import ValidationError

from drugpaper.agents.figure.structure_review import position_policy_gaps
from drugpaper.facts.compound_instances import CompoundInstance


def instance(**overrides):
    value = {
        "reference": "1",
        "scaffold_mol_id": 0,
        "substituents": {"R": "Me"},
        "evidence_text": "printed row",
    }
    value.update(overrides)
    return value


def endpoint(policy, variants, **overrides):
    value = {
        "reference": "1-2",
        "scaffold_mol_id": 0,
        "substituents": {},
        "position_policy": {
            "requirement": policy,
            "scope": "per_member",
            "source": "printed_column",
            "basis": "Table 1 position column",
            "evidence_text": "Table 1",
        },
        "variants": variants,
    }
    value.update(overrides)
    return {"pathways": [{"steps": [{"products": [value]}]}]}


def codes(extraction):
    return [(i["severity"], i["description"]) for i in position_policy_gaps(extraction)]


def test_required_missing_position_blocks_review():
    gaps = position_policy_gaps(endpoint("required", [instance(position_status="missing")]))
    assert gaps and gaps[0]["severity"] == "error"
    assert gaps[0]["field_path"].endswith("/variants/0")


def test_required_not_printed_member_is_allowed():
    assert position_policy_gaps(endpoint("required", [instance(position_status="not_printed")])) == []


def test_not_applicable_without_position_evidence_is_allowed():
    assert (
        position_policy_gaps(endpoint("not_applicable", [instance(position_status="not_applicable")])) == []
    )


def test_not_applicable_with_visible_locant_is_rejected():
    gaps = position_policy_gaps(
        endpoint(
            "not_applicable",
            [instance(position_status="not_applicable", substituents={"R": "2-Cl"})],
        )
    )
    assert gaps and gaps[0]["severity"] == "error"


def test_unknown_position_is_warning_not_blocker():
    gaps = position_policy_gaps(endpoint("unknown", [instance(position_status="missing")]))
    assert gaps and gaps[0]["severity"] == "warning"


def test_exhausted_position_is_accepted_without_retry():
    assert position_policy_gaps(endpoint("exhausted", [instance(position_status="exhausted")])) == []


def test_optional_missing_position_is_warning():
    gaps = position_policy_gaps(endpoint("optional", [instance(position_status="missing")]))
    assert gaps and gaps[0]["severity"] == "warning"


def test_legacy_instance_without_policy_is_not_blocked():
    assert position_policy_gaps({"compound_instances": [instance()]}) == []


def test_instance_status_contradicts_definitions():
    with pytest.raises(ValidationError):
        CompoundInstance(
            reference="1",
            scaffold_mol_id=0,
            substituents={"R": "Me"},
            evidence_text="printed",
            position_status="provided",
        )
    with pytest.raises(ValidationError):
        CompoundInstance(
            reference="1",
            scaffold_mol_id=0,
            substituents={"R": "Me"},
            evidence_text="printed",
            position_status="not_printed",
            position_definitions={"R": "2"},
        )


def test_normalize_position_fields_fills_status_and_upgrades_policy():
    from drugpaper.agents.figure.structure_review import normalize_position_fields

    endpoint = {
        "reference": "1-2",
        "scaffold_mol_id": 1,
        "position_policy": {"requirement": "not_applicable", "basis": "Figure has no locants"},
        "variants": [
            {
                "reference": "1",
                "scaffold_mol_id": 1,
                "substituents": {"R": "Me"},
                "position_definitions": {"R": "2"},
            },
            {"reference": "2", "scaffold_mol_id": 1, "substituents": {"R": "Me"}},
        ],
    }
    state = {
        "extraction": {"pathways": [{"steps": [{"products": [endpoint]}]}]},
        "exploration": {},
    }
    changes = normalize_position_fields(state)
    assert endpoint["position_policy"]["requirement"] == "optional"
    assert endpoint["variants"][0]["position_status"] == "provided"
    assert endpoint["variants"][1]["position_status"] == "missing"
    assert changes
    assert normalize_position_fields(state) == []
