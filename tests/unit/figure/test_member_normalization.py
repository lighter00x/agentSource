import copy

import pytest

from drugpaper.agents.figure.member_normalization import member_normalization_gaps, normalization_checks
from drugpaper.agents.figure.reference_assignments import edit_reference_assignments
from drugpaper.agents.figure.workflow import EditRejected
from tests.unit.figure.test_reference_assignments import PATH, args, source, state


def product(expression="XCHYZ", variables=None):
    return {
        "markush": {
            "variable_options": variables or {"X": ["O", "S"], "Y": ["H", "F"], "Z": ["H", "F"]},
            "substitution_cases": [{"expression": expression}],
        }
    }


def member(group="OCH2F", **changes):
    return {
        "reference": "73",
        "substituents": {"X": "O", "Y": "H", "Z": "F", "XCHYZ": group},
        "attachments": ["XCHYZ"],
        "position_definitions": {},
        "evidence_text": "Source member row gives X=O, Y=H, Z=F",
        **changes,
    }


@pytest.mark.parametrize(
    "x,y,z,correct,wrong",
    [
        ("O", "H", "F", "OCH2F", "OCHF"),
        ("O", "F", "F", "OCHF2", "OCF2"),
        ("S", "H", "F", "SCH2F", "SCHF"),
        ("S", "F", "F", "SCHF2", "SCF2"),
        ("O", "H", "H", "OCH3", "OCH4"),
    ],
)
def test_explicit_atoms_are_neither_lost_nor_added(x, y, z, correct, wrong):
    a = member(substituents={"X": x, "Y": y, "Z": z, "XCHYZ": correct})
    original = copy.deepcopy(a)
    assert normalization_checks(product(), [a])[0]["status"] == "consistent"
    assert a == original
    a["substituents"]["XCHYZ"] = wrong
    check = normalization_checks(product(), [a])[0]
    assert check["status"] == "inconsistent"
    assert check["reason"] == "explicit_atom_count_mismatch"


def test_check_uses_image_expression_not_fixed_variable_names_or_scaffold_labels():
    p = product("QCHAB", {"Q": ["S"], "A": ["H"], "B": ["Br"]})
    a = member(substituents={"Q": "S", "A": "H", "B": "Br", "QCHAB": "SCH2Br"}, attachments=["QCHAB"])
    assert normalization_checks(p, [a])[0]["status"] == "consistent"


@pytest.mark.parametrize(
    "expression,variables,groups",
    [
        ("NRaRb", {"Ra": ["Et"], "Rb": ["Me"]}, {"Ra": "Et", "Rb": "Me", "NRaRb": "N(Et)Me"}),
        ("O(CH2)nR", {"n": ["1", "2"], "R": ["Alk"]}, {"n": "2", "R": "Alk", "O(CH2)nR": "O(CH2)2Alk"}),
        ("XCHYZ", {"X": ["O"], "Y": ["H"], "Z": ["F"]}, {"XCHYZ": "OCH2F"}),
    ],
)
def test_complex_or_incomplete_input_is_left_to_llm_review(expression, variables, groups):
    a = member(substituents=groups, attachments=[expression])
    before = copy.deepcopy(a)
    check = normalization_checks(product(expression, variables), [a])[0]
    assert check["status"] == "not_checked"
    assert a == before


def test_internal_variables_cannot_replace_complete_local_attachment():
    a = member(attachments=["X", "Y", "Z"])
    assert normalization_checks(product(), [a])[0]["reason"] == "internal_variables_selected_as_attachments"
    del a["substituents"]["XCHYZ"]
    assert normalization_checks(product(), [a])[0]["reason"] == "missing_complete_local_group"


@pytest.mark.parametrize("preview", [False, True])
async def test_bad_normalization_rejected_atomically_before_source_review(preview):
    s = state()
    s["extraction"]["pathways"][0]["steps"][0]["products"][0].update(product())
    before = copy.deepcopy(s)
    with pytest.raises(EditRejected) as error:
        await edit_reference_assignments(
            None, source(), s, args(assignments=[member("OCHF")]), preview=preview
        )
    assert error.value.details["code"] == "member_normalization_conflict"
    assert error.value.details["conflicts"][0]["expected_explicit_counts"]["H"] == 2
    assert s == before


def test_structure_audit_flags_existing_wrong_members_even_with_registered_evidence():
    ex = {"pathways": [{"steps": [{"products": [{**product(), "variants": [member("OCHF")]}]}]}]}
    issues = member_normalization_gaps(ex)
    assert len(issues) == 1
    assert issues[0]["field_path"] == PATH + "/variants/0"
    assert issues[0]["severity"] == "error"


async def test_historical_passed_audit_cannot_bypass_new_member_consistency_checks():
    from drugpaper.agents.figure.structure_review import fingerprint, review_structure

    ex = {"pathways": [{"steps": [{"products": [{**product(), "variants": [member("OCHF")]}]}]}]}
    s = {
        "extraction": ex,
        "exploration": {
            "structure_audit": {"status": "passed", "extraction_hash": fingerprint(ex)},
            "structure_reviews": [{}] * 6,
        },
    }
    # Even when no review budget remains, an old passed stamp must not silently
    # certify a contradiction now detectable from the saved source fields.
    with pytest.raises(ValueError, match="budget exhausted"):
        await review_structure(None, s)
