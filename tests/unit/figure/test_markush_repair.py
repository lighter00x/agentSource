import copy
from types import MethodType

import pytest

from drugpaper.agents.figure.markush_repair import build_cases, local_group, repair_markush_cases
from drugpaper.chem.product_candidates import combinations
from drugpaper.skills.loader import load_package
from tests.unit.figure.test_scheme_series import ROOT
from tests.unit.figure.test_structure_review import PRODUCT, state


def product():
    return {
        "scaffold_mol_id": 1,
        "markush": {
            "variable_options": {"Q": ["O", "S"], "U": ["H", "F"], "V": ["H", "F"]},
            "substitution_cases": [],
        },
    }


def inventory(*labels):
    return {"structures": [{"mol_id": 1, "attachment_labels": list(labels)}]}


def test_composite_cases_keep_explicit_hydrogen_and_equivalent_assignments():
    p = product()
    p["markush"]["substitution_cases"] = build_cases(p, ["QCHUV"])
    cases = p["markush"]["substitution_cases"]
    assert {c["group_text"] for c in cases} == {"OCH3", "OCH2F", "OCHF2", "SCH3", "SCH2F", "SCHF2"}
    assert len(cases) == 6
    assert sum(len(c["assignments"]) for c in cases) == 8
    assert all(c["expression"] == "QCHUV" for c in cases)
    compiled, raw = combinations(p["markush"])
    assert (len(compiled), raw) == (6, 8)
    assert local_group("NRaRb", {"Ra": "H", "Rb": "H"}) == "NHH"


def test_separate_attachment_sites_are_not_invented_joint_expressions():
    p = product()
    p["markush"]["variable_options"] = {"Ra": ["H", "Cl"], "Rb": ["CH", "N"]}
    p["markush"]["substitution_cases"] = build_cases(p, ["Ra", "Rb"])
    assert {c["expression"] for c in p["markush"]["substitution_cases"]} == {"Ra", "Rb"}
    assert len(combinations(p["markush"])[0]) == 4


@pytest.mark.parametrize("labels", [[], ["Unknown"], ["Q"]])
def test_unknown_or_incomplete_attachment_coverage_is_rejected(labels):
    with pytest.raises(ValueError):
        build_cases(product(), labels)


def args(**changes):
    return {
        "product_path": PRODUCT,
        "expected_version": 1,
        "evidence_ids": ["e1"],
        "note": "Repair only the declared visible local choices",
        "expressions": ["R"],
        **changes,
    }


def test_repair_changes_only_selected_cases_and_status():
    s = state()
    before = copy.deepcopy(s["extraction"])
    result = repair_markush_cases(s, args(), load_package(ROOT).output_schema, inventory("R"))
    expected = copy.deepcopy(before)
    target = expected["pathways"][0]["steps"][0]["products"][0]["markush"]
    target["substitution_cases"] = result["after"]["substitution_cases"]
    assert s["extraction"] == expected
    assert len(target["substitution_cases"]) == 2
    assert target["substitution_cases"][0]["basis"] == "printed"
    assert s["exploration"]["extraction_version"] == 2
    assert "structure_audit" not in s["exploration"]
    assert result["before_hash"] != result["after_hash"]


@pytest.mark.parametrize(
    "changes",
    [
        {"expected_version": 2},
        {"evidence_ids": ["other"]},
        {"evidence_ids": ["preview"]},
        {"product_path": "/pathways/0/steps/9/products/0"},
    ],
)
def test_rejected_repair_preserves_all_state(changes):
    s = state()
    before = copy.deepcopy(s)
    with pytest.raises(ValueError):
        repair_markush_cases(s, args(**changes), load_package(ROOT).output_schema, inventory("R"))
    assert s == before


async def test_agent_dispatches_local_repair_then_reviews_before_backfill(figure_env, monkeypatch):
    from drugpaper.agents.figure import exploration
    from drugpaper.agents.figure.structure_review import audit_passed
    from tests.unit.figure.test_exploration import harness
    from tests.unit.figure.test_structure_review import audit_payload, extraction

    monkeypatch.setattr(
        exploration, "structure_placeholder_inventory", lambda _: {"structures": [{"mol_id": 1}]}
    )
    agent, s, _ = await harness(
        figure_env,
        [
            ("repair_markush_cases", args(expected_version=7)),
            ("review_structure", {}),
            ("validate_result", {}),
        ],
    )
    agent.services.settings.figure_exploration_max_turns = 3
    s.update(chain=["scheme_extract"], extraction=extraction(), resume_structure=True)
    s["exploration"] = {
        "extraction_version": 7,
        "evidence": [{"id": "e1", "tool": "inspect_visual", "result": {"node_ref": s["node_ref"]}}],
        "applied_evidence_ids": ["e1"],
        "gaps": [],
    }
    reviews = []

    async def forbidden(self, state):
        raise AssertionError("The full extraction must not run again")

    async def skill(self, state, name, inputs, **kwargs):
        assert name == "structure_audit"
        reviews.append(True)
        return audit_payload(failed=len(reviews) == 1)

    async def post(self, state):
        assert audit_passed(state)
        state["backfilled_extraction"] = copy.deepcopy(state["extraction"])
        return state

    agent._structure_once = MethodType(forbidden, agent)
    agent._run_skill = MethodType(skill, agent)
    agent._post = MethodType(post, agent)
    await exploration.explore(agent, s)
    assert len(reviews) == 2
    assert s["exploration"]["extraction_attempts"] == 0
    assert s["exploration"]["structure_patches"][0]["kind"] == "symbolic_markush_repair"
    assert audit_passed(s)
    assert "backfilled_extraction" in s
