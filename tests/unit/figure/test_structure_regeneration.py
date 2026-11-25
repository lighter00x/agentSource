"""Broad-error recovery preserves recognition, evidence, old versions and budgets."""

import copy
from types import MethodType, SimpleNamespace

import pytest

from drugpaper.agents.figure.structure_regeneration import regenerate_structure
from drugpaper.agents.figure.structure_review import audit_passed, fingerprint
from drugpaper.skills.loader import load_package
from tests.unit.figure.test_exploration import harness
from tests.unit.figure.test_scheme_series import ROOT
from tests.unit.figure.test_structure_review import audit_payload, case, extraction, state


def args(**updates):
    return {
        "expected_version": 1,
        "damage_kind": "topology_error",
        "issue_ids": ["t1"],
        "evidence_ids": ["e1"],
        "note": "The main route topology is wrong throughout; isolated scalar patches cannot restore the route coherently.",
        **updates,
    }


def bad_audit(payload):
    review = audit_payload()
    for c in review["coverage"]:
        if c["dimension"] == "topology":
            c["verdict"] = "fail"
    review["issues"] = [
        {
            "issue_id": "t1",
            "field_path": "/pathways",
            "category": "incorrect_topology",
            "severity": "error",
            "description": "Main route order and branches are incorrect",
            "visible_evidence": "Compare arrows on current target image",
            "suggested_action": "Reconstruct topology",
        }
    ]
    return {**review, "status": "needs_revision", "extraction_hash": fingerprint(payload)}


def ready():
    s = state()
    s["exploration"].update(extractions=1, extraction_attempts=1, structure_audit=bad_audit(s["extraction"]))
    return s


def candidate():
    value = extraction()
    value["pathways"][0]["steps"][0]["relationship_type"] = "optimization"
    value["pathways"][0]["steps"][0]["products"][0]["markush"]["substitution_cases"].append(case("Cl"))
    return value


def agent_with(worker):
    return SimpleNamespace(
        services=SimpleNamespace(
            settings=SimpleNamespace(
                figure_exploration_max_regenerations=1, figure_exploration_max_extractions=3
            )
        ),
        _structure_once=worker,
    )


async def test_regeneration_archives_version_reuses_material_and_invalidates_delivery():
    s = ready()
    s["material"] = {"structures": [{"mol_id": 0, "recognized": "existing"}]}
    s["backfilled_extraction"] = {"stale": True}
    old = copy.deepcopy(s["extraction"])

    async def worker(pending):
        assert pending["material"] == s["material"]
        assert pending["exploration"]["evidence"] == s["exploration"]["evidence"]
        assert pending["exploration"]["regeneration_context"]["issues"][0]["issue_id"] == "t1"
        pending["extraction"] = candidate()

    record = await regenerate_structure(agent_with(worker), s, args(), load_package(ROOT).output_schema)
    assert record["new_version"] == 2 and record["status"] == "awaiting_review"
    assert s["exploration"]["structure_versions"][0]["extraction"] == old
    assert s["exploration"]["extractions"] == 2
    assert "backfilled_extraction" not in s
    assert not audit_passed(s)
    assert s["material"]["structures"][0]["recognized"] == "existing"


@pytest.mark.parametrize(
    "change",
    [
        {"expected_version": 2},
        {"issue_ids": ["invented"]},
        {"evidence_ids": ["preview"]},
        {"damage_kind": "widespread_placeholder_error"},
    ],
)
async def test_unjustified_regeneration_never_calls_model_or_changes_state(change):
    s = ready()
    before = copy.deepcopy(s)

    async def worker(pending):
        pytest.fail("Rejected regeneration must not call the model")

    with pytest.raises(ValueError):
        await regenerate_structure(agent_with(worker), s, args(**change), load_package(ROOT).output_schema)
    assert s == before


@pytest.mark.parametrize("failure", ["invalid", "empty", "unchanged", "timeout"])
async def test_failed_candidate_keeps_original_and_spends_retry_budget(failure):
    s = ready()
    old = copy.deepcopy(s["extraction"])

    async def worker(pending):
        pending["accounting"] = {"paid_call": True}
        if failure == "timeout":
            raise TimeoutError("test")
        if failure == "empty":
            pending["extraction"] = {"status": "unclear", "pathways": []}
        elif failure == "invalid":
            pending["extraction"] = {"status": "bad"}

    with pytest.raises((ValueError, TimeoutError)):
        await regenerate_structure(agent_with(worker), s, args(), load_package(ROOT).output_schema)
    assert s["extraction"] == old and s["exploration"]["extraction_version"] == 1
    assert s["accounting"] == {"paid_call": True}
    assert s["exploration"]["structure_regenerations"][0]["status"] == "failed"
    with pytest.raises(ValueError, match="budget exhausted"):
        await regenerate_structure(agent_with(worker), s, args(), load_package(ROOT).output_schema)


@pytest.mark.parametrize("review_passed", [True, False])
async def test_agent_dispatch_rebuilds_then_reviews_before_delivery(figure_env, review_passed):
    agent, s, model = await harness(
        figure_env,
        [
            ("inspect_visual", {"node_id": "doc_chem/r1/p000.g000"}),
            ("extract_figure", {}),
            ("regenerate_structure", args()),
            ("review_structure", {}),
            ("validate_result", {}),
        ],
    )
    s["chain"] = ["scheme_extract"]
    agent.services.settings.figure_exploration_max_turns = 5
    calls, reviews, delivered = [], [], []

    async def extract(self, state):
        calls.append(True)
        state["extraction"] = extraction() if len(calls) == 1 else candidate()

    async def skill(self, state, name, inputs, **kwargs):
        assert name == "structure_audit"
        reviews.append(True)
        value = bad_audit(state["extraction"]) if len(reviews) == 1 or not review_passed else audit_payload()
        return {k: v for k, v in value.items() if k not in {"status", "extraction_hash"}}

    async def post(self, state):
        assert audit_passed(state)
        delivered.append(True)

    agent._structure_once = MethodType(extract, agent)
    agent._run_skill = MethodType(skill, agent)
    agent._post = MethodType(post, agent)
    from drugpaper.agents.figure.exploration import explore

    await explore(agent, s)
    outputs = {e["name"]: e["result"] for e in s["exploration"]["steps"] if e.get("kind") == "tool"}
    assert outputs["regenerate_structure"]["ok"], outputs
    assert outputs["regenerate_structure"]["regeneration"]["review_status"] == "pending"
    assert outputs["review_structure"]["structure_review"]["status"] == (
        "passed" if review_passed else "needs_revision"
    )
    assert len(calls) == 2 and len(reviews) == 2
    assert len(delivered) == int(review_passed)
    assert outputs["validate_result"]["ok"] == review_passed
    assert len(s["exploration"]["structure_versions"]) == 1
    assert "regenerate_structure" in {tool.name for tool in model.requests[2].tools}
    assert "extract_figure" not in {tool.name for tool in model.requests[2].tools}


@pytest.mark.parametrize("ambiguous", [False, True])
async def test_confirmed_member_evidence_follows_unique_identity_not_old_array_index(ambiguous):
    s = ready()
    old_product = s["extraction"]["pathways"][0]["steps"][0]["products"][0]
    old_product["reference"] = "family"
    s["exploration"]["structure_audit"] = bad_audit(s["extraction"])
    s["exploration"]["reference_assignments"] = [
        {
            "supplement_id": "ra1",
            "product_path": "/pathways/0/steps/0/products/0",
            "scaffold_mol_id": 1,
            "target_reference": "family",
            "sources": [{"evidence_id": "e1", "node_ref": s["node_ref"]}],
            "assignments": [
                {
                    "reference": "member",
                    "substituents": {"R": "Cl"},
                    "attachments": ["R"],
                    "position_definitions": {},
                    "evidence_text": "Verified member choice",
                }
            ],
        }
    ]
    old = copy.deepcopy(s["extraction"])

    async def worker(pending):
        value = candidate()
        step = value["pathways"][0]["steps"][0]
        step["products"][0]["reference"] = "family"
        before = copy.deepcopy(step)
        if not ambiguous:
            before["products"] = [{"mol_id": 0, "reference": "intermediate"}]
        value["pathways"][0]["steps"].insert(0, before)
        pending["extraction"] = value

    if ambiguous:
        with pytest.raises(ValueError, match="uniquely"):
            await regenerate_structure(agent_with(worker), s, args(), load_package(ROOT).output_schema)
        assert s["extraction"] == old
        assert (
            s["exploration"]["reference_assignments"][0]["product_path"] == "/pathways/0/steps/0/products/0"
        )
    else:
        await regenerate_structure(agent_with(worker), s, args(), load_package(ROOT).output_schema)
        assert (
            s["exploration"]["reference_assignments"][0]["product_path"] == "/pathways/0/steps/1/products/0"
        )
        member = s["extraction"]["pathways"][0]["steps"][1]["products"][0]["variants"][0]
        assert member["reference"] == "member" and member["substituents"] == {"R": "Cl"}


async def test_local_markush_omission_requires_patch_not_full_regeneration():
    s = ready()
    audit = audit_payload(failed=True)
    s["exploration"]["structure_audit"] = {
        **audit,
        "status": "needs_revision",
        "extraction_hash": fingerprint(s["extraction"]),
    }

    async def worker(pending):
        pytest.fail("Local omissions should not regenerate")

    with pytest.raises(ValueError, match="local defects"):
        await regenerate_structure(
            agent_with(worker), s, args(issue_ids=["m1"]), load_package(ROOT).output_schema
        )


async def test_cancelled_regeneration_restores_emergency_state_and_preserves_old_structure():
    import asyncio

    s = ready()
    before = copy.deepcopy(s["extraction"])
    agent = agent_with(None)

    async def worker(pending):
        agent.progress = pending
        pending["extraction"] = {"uncommitted": True}
        raise asyncio.CancelledError()

    agent._structure_once = worker
    with pytest.raises(asyncio.CancelledError):
        await regenerate_structure(agent, s, args(), load_package(ROOT).output_schema)
    assert agent.progress is s
    assert s["extraction"] == before
    assert s["exploration"]["structure_regenerations"][0]["status"] == "failed"
