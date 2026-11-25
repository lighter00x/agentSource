import copy
from types import MethodType, SimpleNamespace

import pytest
from jsonschema.exceptions import ValidationError as JsonSchemaError

from drugpaper.agents.figure.graph import FigureAgent
from drugpaper.agents.figure.structure_review import (
    DIMENSIONS,
    attachment_selection_gaps,
    attachment_selection_summary,
    audit_passed,
    candidate_definition_gaps,
    composite_markush_gaps,
    fingerprint,
    get_structure,
    patch_structure,
    preview_structure_patch,
    series_relation_gaps,
    topology_summary,
)
from drugpaper.skills.loader import load_package
from drugpaper.skills.registry import BUILTIN_ROOT
from tests.unit.figure.test_exploration import harness
from tests.unit.figure.test_scheme_series import ROOT

PRODUCT = "/pathways/0/steps/0/products/0"
CASE_PATH = PRODUCT + "/markush/substitution_cases"


def test_structure_reader_cannot_recover_hidden_chemical_values_by_path():
    s = {
        "extraction": {"mol_id": 3, "canonical_smiles": "PRIVATE", "formula": "PRIVATE_FORMULA"},
        "exploration": {"extraction_version": 1},
    }
    result = get_structure(s, {})
    assert "PRIVATE" not in str(result)
    assert result["value"] == {"mol_id": 3}
    with pytest.raises(ValueError, match="does not exist"):
        get_structure(s, {"path": "/canonical_smiles"})


def case(group="H"):
    return {
        "expression": "R",
        "group_text": group,
        "assignments": [{"R": group}],
        "basis": "printed",
        "evidence_text": "Current figure prints R = H, Cl",
    }


def extraction():
    return {
        "status": "ok",
        "pathways": [
            {
                "steps": [
                    {
                        "relationship_type": "reaction",
                        "reactants": [{"mol_id": 0, "reference": "A"}],
                        "products": [
                            {
                                "scaffold_mol_id": 1,
                                "markush": {
                                    "description": "Product family",
                                    "variable_options": {"R": ["H", "Cl"]},
                                    "substitution_cases": [case()],
                                    "expansion_status": "complete_for_visible_options",
                                    "notes": "Only current figure choices",
                                    "evidence_text": "Printed R = H, Cl",
                                },
                            }
                        ],
                    }
                ]
            }
        ],
    }


def state():
    return {
        "node_ref": "d/r1/p000.g000",
        "chain": ["scheme_extract"],
        "extraction": extraction(),
        "exploration": {
            "extraction_version": 1,
            "evidence": [
                {"id": "e1", "tool": "inspect_visual", "result": {"node_ref": "d/r1/p000.g000"}},
                {"id": "preview", "tool": "preview_pages", "result": {}},
                {"id": "other", "tool": "inspect_visual", "result": {"node_ref": "d/r1/p003.g000"}},
            ],
            "structure_audit": {"issues": [{"issue_id": "m1"}]},
        },
    }


def arguments(**updates):
    return {
        "expected_version": 1,
        "operations": [{"op": "add", "path": CASE_PATH + "/-", "value": case("Cl")}],
        "evidence_ids": ["e1"],
        "issue_ids": ["m1"],
        "note": "Append the missing visible chlorine choice",
        **updates,
    }


def apply(s, args):
    return patch_structure(
        s, args, load_package(ROOT).output_schema, {"structures": [{"mol_id": 0}, {"mol_id": 1}]}
    )


def audit_payload(failed=False):
    return {
        "coverage": [
            {
                "dimension": d,
                "verdict": "fail" if failed and d == "markush_completeness" else "pass",
                "evidence": "Compare original target drawing and variable legend",
            }
            for d in sorted(DIMENSIONS)
        ],
        "issues": [
            {
                "issue_id": "m1",
                "field_path": CASE_PATH,
                "category": "missing_markush_case",
                "severity": "error",
                "description": "Missing chlorine option",
                "visible_evidence": "Legend explicitly says H, Cl",
                "suggested_action": "Append the missing Cl case only",
            }
        ]
        if failed
        else [],
    }


def test_append_preserves_all_other_content_and_records_exact_diff():
    s = state()
    original = copy.deepcopy(s["extraction"])
    record = apply(s, arguments())
    expected = copy.deepcopy(original)
    expected["pathways"][0]["steps"][0]["products"][0]["markush"]["substitution_cases"].append(case("Cl"))
    assert s["extraction"] == expected
    assert record["changes"] == [{"path": CASE_PATH + "/-", "before": None, "after": case("Cl")}]
    assert s["exploration"]["extraction_version"] == 2
    assert "structure_audit" not in s["exploration"]


def test_replace_scalar_selector_list_is_local_and_preserves_product():
    s = state()
    product = s["extraction"]["pathways"][0]["steps"][0]["products"][0]
    product.pop("markush")
    product.update(substituents={"R": "Cl"}, attachments=["wrong"])
    before = copy.deepcopy(s["extraction"])
    record = apply(
        s, arguments(operations=[{"op": "replace", "path": PRODUCT + "/attachments", "value": ["R"]}])
    )
    before["pathways"][0]["steps"][0]["products"][0]["attachments"] = ["R"]
    assert s["extraction"] == before
    assert len(record["changes"]) == 1


def test_reviewer_selector_summary_reports_present_fields_without_changing_them():
    data = {
        "variants": [
            {
                "scaffold_mol_id": 7,
                "reference": "lead-Q",
                "substituents": {"side": "Me", "ring": "N"},
                "attachments": ["side", "ring"],
            }
        ]
    }
    before = copy.deepcopy(data)
    summary = attachment_selection_summary(
        data, {"structures": [{"mol_id": 7, "attachment_labels": ["side", "ring"]}]}
    )
    assert summary[0]["attachments_present"]
    assert summary[0]["attachments"] == summary[0]["expected_sites"] == ["side", "ring"]
    assert summary[0]["path"] == "/variants/0"
    assert data == before


def test_topology_summary_keeps_nested_members_separate_from_arrow_endpoints():
    data = {
        "pathways": [
            {
                "steps": [
                    {
                        "relationship_type": "optimization",
                        "reactants": [{"mol_id": 8}],
                        "products": [
                            {"scaffold_mol_id": 9, "reference": "family", "variants": [{"reference": "lead"}]}
                        ],
                    }
                ]
            }
        ]
    }
    before = copy.deepcopy(data)
    summary = topology_summary(data)
    assert len(summary) == 1 and summary[0]["path"] == "/pathways/0/steps/0"
    assert summary[0]["reactants"][0]["mol_id"] == 8
    assert summary[0]["products"] == [{"mol_id": None, "scaffold_mol_id": 9, "reference": "family"}]
    assert data == before


def test_series_focus_does_not_inherit_whole_figure_optimization_theme():
    step = {
        "relationship_type": "optimization",
        "reactants": [{"compound_series": {"members": ["lead-A", "lead-B"]}}],
        "products": [{"reference": "lead-B"}],
        "conditions": None,
    }
    data = {"pathways": [{"steps": [step]}]}
    assert series_relation_gaps(data)[0]["field_path"] == "/pathways/0/steps/0/relationship_type"
    step["relationship_type"] = "selection"
    assert series_relation_gaps(data) == []
    step.update(relationship_type="optimization", conditions="Explicit conversion condition")
    assert series_relation_gaps(data) == []


def test_read_structure_paginates_and_escapes_real_keys_without_mutating():
    s = state()
    s["extraction"]["extra"] = {"a/b~c": {"x": 1}}
    original = copy.deepcopy(s)
    result = get_structure(s, {"path": "/extra"})
    assert result["children"][0]["path"] == "/extra/a~1b~0c"
    assert get_structure(s, {"path": "/extra/a~1b~0c/x"})["value"] == 1
    page = get_structure(s, {"path": PRODUCT + "/markush", "limit": 1})
    assert page["has_more"] and page["value_omitted"]
    assert "value" not in page
    assert page["extraction_version"] == 1
    assert s == original
    with pytest.raises(ValueError, match="does not exist"):
        get_structure(s, {"path": "/missing"})


def test_read_accepts_optional_version_precondition_and_rejects_stale_version():
    s = state()
    assert get_structure(s, {"path": PRODUCT, "expected_version": 1})["extraction_version"] == 1
    with pytest.raises(ValueError, match="version changed"):
        get_structure(s, {"path": PRODUCT, "expected_version": 2})


def test_preview_runs_real_validation_but_preserves_state_and_review():
    s = state()
    original = copy.deepcopy(s)
    preview = preview_structure_patch(
        s, arguments(), load_package(ROOT).output_schema, {"structures": [{"mol_id": 0}, {"mol_id": 1}]}
    )
    assert preview["dry_run"] and preview["extraction_version"] == 1
    assert s == original
    assert apply(s, arguments())["changes"] == preview["patch"]["changes"]


def test_failed_test_operation_rolls_back_previous_operations():
    s = state()
    original = copy.deepcopy(s)
    with pytest.raises(ValueError, match="precondition failed"):
        apply(
            s,
            arguments(
                operations=[
                    *arguments()["operations"],
                    {"op": "test", "path": PRODUCT + "/scaffold_mol_id", "value": 999},
                ]
            ),
        )
    assert s == original


def test_test_operation_checks_old_value_before_add_replace_and_remove():
    s = state()
    record = apply(
        s,
        arguments(
            operations=[
                {"op": "test", "path": CASE_PATH + "/0/group_text", "value": "H"},
                *arguments()["operations"],
                {
                    "op": "replace",
                    "path": CASE_PATH + "/0/evidence_text",
                    "value": "Verified original option",
                },
                {"op": "remove", "path": CASE_PATH + "/0"},
            ]
        ),
    )
    assert len(record["changes"]) == 3
    cases = get_structure(s, {"path": CASE_PATH})["value"]
    assert cases == [case("Cl")]


@pytest.mark.parametrize(
    "change",
    [
        {"expected_version": 2},
        {"evidence_ids": ["preview"]},
        {"evidence_ids": ["invented"]},
        {"evidence_ids": ["other"]},
        {"issue_ids": ["invented"]},
        {"operations": [{"op": "replace", "path": "/pathways", "value": []}]},
        {"operations": [{"op": "replace", "path": PRODUCT + "/markush", "value": {}}]},
        {"operations": [{"op": "replace", "path": PRODUCT + "/scaffold_mol_id", "value": 999}]},
        {"operations": [{"op": "add", "path": PRODUCT + "/smiles", "value": "CC"}]},
        {"operations": [{"op": "add", "path": CASE_PATH + "/-", "value": case("Br")}]},
        {"operations": [{"op": "replace", "path": "/status"}]},
    ],
)
def test_invalid_patch_is_transactional(change):
    s = state()
    original = copy.deepcopy(s)
    with pytest.raises((ValueError, JsonSchemaError)):
        apply(s, arguments(**change))
    assert s == original


def test_review_is_bound_to_exact_extraction_and_invalidated_by_edit():
    s = state()
    s["exploration"]["structure_audit"] = {
        "status": "passed",
        "extraction_hash": fingerprint(s["extraction"]),
    }
    assert audit_passed(s)
    s["extraction"]["pathways"][0]["steps"][0]["relationship_type"] = "optimization"
    assert not audit_passed(s)


async def test_unchanged_structure_reuses_its_review_without_spending_budget():
    from drugpaper.agents.figure.structure_review import review_structure

    s = state()
    previous = {
        "status": "needs_revision",
        "extraction_hash": fingerprint(s["extraction"]),
        "issues": [{"issue_id": "missing-visible-choice"}],
    }
    s["exploration"]["structure_audit"] = copy.deepcopy(previous)
    s["exploration"]["structure_reviews"] = [previous] * 6
    events = []

    async def capture(state, event):
        events.append(event)

    assert await review_structure(SimpleNamespace(_exploration_event=capture), s) == previous
    assert events[0]["kind"] == "structure_review_cached"
    assert events[0]["reused"] is True
    assert len(s["exploration"]["structure_reviews"]) == 6


async def test_reviewer_receives_registered_source_summary_not_extra_reference_images(figure_env):
    from drugpaper.agents.figure.structure_review import review_structure

    agent, s, _ = await harness(figure_env, [])
    s.update(state())
    s["node_ref"] = "doc_chem/r1/p000.g000"
    product = s["extraction"]["pathways"][0]["steps"][0]["products"][0]
    product["reference"] = "family"
    product["variants"] = [{"reference": "member", "scaffold_mol_id": 1, "substituents": {"R": "H"}}]
    record = {
        "supplement_id": "ra1",
        "product_path": PRODUCT,
        "scaffold_mol_id": 1,
        "target_reference": "family",
        "sources": [{"evidence_id": "e1", "node_ref": "doc_chem/r1/p007.g000", "pages": [7]}],
        "assignments": [{"reference": "member", "substituents": {"R": "H"}, "position_definitions": {}}],
    }
    s["exploration"].update(reference_assignments=[record], applied_evidence_ids=["e1"])
    captured = []

    async def skill(self, state, name, inputs, **kwargs):
        assert name == "structure_audit"
        captured.append(inputs["reference_source_summary"])
        return audit_payload()

    agent._run_skill = MethodType(skill, agent)
    await review_structure(agent, s)
    assert captured[0]["constraint_error_count"] == 0
    assert captured[0]["records"][0]["references"] == ["member"]
    assert captured[0]["records"][0]["sources"] == record["sources"]


async def test_unapproved_structure_never_calls_backfill():
    s = state()
    s["material"] = {}
    await FigureAgent._post(SimpleNamespace(), s)
    assert s["material"]["backfill_errors"][0]["code"] == "structure_review_blocked"
    assert s["backfilled_extraction"]["pathways"] == s["extraction"]["pathways"]
    assert s["molecular_coverage"]["delivery"] == "flowchart_only"
    assert s["molecular_coverage"]["status"] == "pending_review"


async def test_reviewer_request_excludes_reference_page_images():
    import json

    from drugpaper.gateway.base import ChatResponse
    from drugpaper.skills.execution import execute
    from tests.unit.figure.test_scheme_series import context

    ctx = context()
    ctx.extras["document_evidence_images"] = [
        {
            "tool": "inspect_visual",
            "result": {
                "node_ref": "other/r1/p099.g000",
                "image_refs": [{"blob_ref": "must-not-read-reference-image"}],
            },
        }
    ]

    class Reviewer:
        async def chat(self, request, *, task):
            assert task == "figure_structure_review"
            assert "must-not-read-reference-image" not in request.model_dump_json(exclude={"response_schema"})
            return ChatResponse(content=json.dumps(audit_payload()))

    ctx.model = Reviewer()
    result = await execute(
        load_package(BUILTIN_ROOT / "structure_audit"),
        {
            "extraction": extraction(),
            "extraction_rules": "Review current figure declarations",
            "structure_inventory": {},
            "scope": "current_target_figure_only",
        },
        ctx,
    )
    assert len(result.artifacts[0].payload["coverage"]) == 4


async def test_native_review_patch_recheck_precedes_any_assembly(figure_env):
    agent, s, _ = await harness(
        figure_env,
        [
            ("inspect_visual", {"node_id": "doc_chem/r1/p000.g000"}),
            ("extract_figure", {}),
            ("validate_result", {}),
            ("get_structure", {"path": CASE_PATH}),
            ("preview_structure_patch", arguments()),
            ("patch_structure", arguments()),
            ("review_structure", {}),
            ("validate_result", {}),
        ],
    )
    s["chain"] = ["scheme_extract"]
    agent.services.settings.figure_exploration_max_turns = 8
    audits, deliveries, extracts = [], [], []

    async def extract(self, state):
        extracts.append(True)
        state["extraction"] = extraction()

    async def skill(self, state, name, inputs, **kwargs):
        assert name == "structure_audit"
        assert inputs["scope"] == "current_target_figure_only"
        assert "document_evidence" not in inputs
        audits.append(copy.deepcopy(inputs))
        return audit_payload(failed=len(audits) == 1)

    async def post(self, state):
        assert audit_passed(state)
        deliveries.append(True)

    agent._structure_once = MethodType(extract, agent)
    agent._run_skill = MethodType(skill, agent)
    agent._post = MethodType(post, agent)
    from drugpaper.agents.figure.exploration import explore

    await explore(agent, s)
    assert len(extracts) == 1 and len(audits) == 2 and len(deliveries) == 1
    assert (
        len(s["extraction"]["pathways"][0]["steps"][0]["products"][0]["markush"]["substitution_cases"]) == 2
    )
    assert s["exploration"]["structure_audit"]["status"] == "passed"
    errors = [x for x in s["exploration"]["steps"] if x.get("kind") == "tool" and not x["result"].get("ok")]
    assert (
        errors[0]["name"] == "validate_result"
        and "Structure review must pass" in errors[0]["result"]["error"]
    )

    returned = {x["name"]: x["result"] for x in s["exploration"]["steps"] if x.get("kind") == "tool"}
    assert returned["get_structure"]["extraction_version"] == 1
    assert returned["preview_structure_patch"]["dry_run"]
    assert returned["preview_structure_patch"]["extraction_version"] == 1
    assert returned["patch_structure"]["extraction_version"] == 2


async def test_fixed_mode_also_reviews_structure_before_delivery(figure_env):
    agent, s, _ = await harness(figure_env, [])
    agent.services.settings.figure_exploration_enabled = False
    s["chain"] = ["scheme_extract"]

    async def extract(self, state):
        state["extraction"] = extraction()
        state["extraction"]["pathways"][0]["steps"][0]["products"][0]["markush"]["substitution_cases"].append(
            case("Cl")
        )

    async def skill(self, state, name, inputs, **kwargs):
        assert name == "structure_audit"
        return audit_payload()

    agent._structure_once = MethodType(extract, agent)
    agent._run_skill = MethodType(skill, agent)
    await FigureAgent._structure(agent, s)
    assert audit_passed(s)
    assert s["exploration"]["structure_review_required"] is True


def test_composite_attachment_requires_joint_cases_instead_of_separate_variable_lists():
    raw = extraction()
    markush = raw["pathways"][0]["steps"][0]["products"][0]["markush"]
    markush["variable_options"] = {"Ra": ["H", "Me"], "Rb": ["H", "Me"]}
    markush["substitution_cases"] = [
        {"expression": name, "group_text": option, "assignments": [{name: option}]}
        for name, options in markush["variable_options"].items()
        for option in options
    ]
    inventory = {"structures": [{"mol_id": 1, "attachment_labels": ["NRaRb"]}]}
    assert composite_markush_gaps(raw, inventory) == []  # Recognition labels do not constrain this stage.
    # Chemistry, symmetry and completeness remain independent visual review duties.
    markush["substitution_cases"] = [
        {"expression": "NRaRb", "group_text": "NH2", "assignments": [{"Ra": "H", "Rb": "H"}]}
    ]
    assert composite_markush_gaps(raw, inventory) == []
    markush["substitution_cases"] = []
    inventory["structures"][0]["attachment_labels"] = ["Ra", "Rb"]
    assert composite_markush_gaps(raw, inventory) == []


def test_composite_explicit_atoms_cannot_be_duplicated_in_group_text():
    raw = extraction()
    markush = raw["pathways"][0]["steps"][0]["products"][0]["markush"]
    markush["variable_options"] = {"Ra": ["H"], "Rb": ["H"]}
    markush["substitution_cases"] = [
        {"expression": "NRaRb", "group_text": "NH3", "assignments": [{"Ra": "H", "Rb": "H"}]}
    ]
    inventory = {"structures": [{"mol_id": 1, "attachment_labels": ["NRaRb"]}]}
    assert composite_markush_gaps(raw, inventory)[0]["category"] == "incorrect_markush_case"
    markush["substitution_cases"][0]["group_text"] = "NH2"
    assert composite_markush_gaps(raw, inventory) == []


def test_candidate_review_ignores_recognition_labels_but_keeps_definition_checks():
    raw = extraction()
    markush = raw["pathways"][0]["steps"][0]["products"][0]["markush"]
    markush["variable_options"] = {"R": ["H"], "X": ["Cl"], "Y": ["CH"]}
    markush["substitution_cases"] = [
        {"expression": k, "group_text": v[0], "assignments": [{k: v[0]}]}
        for k, v in markush["variable_options"].items()
    ]
    incomplete_inventory = {"structures": [{"mol_id": 1, "attachment_labels": ["R", "Y"]}]}
    assert candidate_definition_gaps(raw, incomplete_inventory) == []
    markush["substitution_cases"].pop()
    assert candidate_definition_gaps(raw, incomplete_inventory)


def test_attachment_review_checks_recognized_sites_and_declared_keys():
    raw = {
        "variants": [
            {
                "scaffold_mol_id": 1,
                "attachments": ["R", "Y"],
                "substituents": {"R": "H", "X": "Cl", "Y": "CH"},
            }
        ]
    }
    inventory = {"structures": [{"mol_id": 1, "attachment_labels": ["R", "Y"]}]}
    assert attachment_selection_gaps(raw, inventory) == []
    raw["variants"][0]["attachments"] = ["R", "X", "Y"]
    assert attachment_selection_gaps(raw, inventory)
    raw["variants"][0]["attachments"] = ["R"]
    assert attachment_selection_gaps(raw, inventory)
    raw["variants"][0]["attachments"] = ["R", "Y", "missing"]
    assert attachment_selection_gaps(raw, inventory)


async def test_resume_existing_structure_never_regenerates_json(figure_env):
    agent, s, model = await harness(
        figure_env,
        [
            ("patch_structure", arguments(expected_version=7)),
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
        raise AssertionError("Existing extraction must never be regenerated")

    async def skill(self, state, name, inputs, **kwargs):
        assert name == "structure_audit"
        reviews.append(True)
        return audit_payload(failed=len(reviews) == 1)

    agent._structure_once = MethodType(forbidden, agent)
    agent._run_skill = MethodType(skill, agent)
    from drugpaper.agents.figure.exploration import explore

    await explore(agent, s)
    assert len(reviews) == 2
    assert s["exploration"]["extraction_attempts"] == 0
    assert s["exploration"]["extraction_version"] == 8
    assert audit_passed(s)
    for request in model.requests:
        assert "extract_figure" not in {tool.name for tool in request.tools}
        assert "finish" not in {tool.name for tool in request.tools}
