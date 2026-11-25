"""Recovery contracts use arbitrary labels and source locations, not a paper-specific route."""

import copy
from types import SimpleNamespace

import pytest
from jsonschema.exceptions import ValidationError as SchemaError

from drugpaper.agents.figure.reference_assignments import (
    edit_reference_assignments,
    record_reference_assignments,
)
from drugpaper.agents.figure.structure_review import local_patch_count
from drugpaper.agents.figure.workflow import (
    EditRejected,
    ProgressTracker,
    recovery_error,
    restore_evidence,
    track_edit,
    workflow_status,
)
from drugpaper.skills.loader import load_package
from drugpaper.storage.localfs import LocalFsBlobStore
from tests.unit.figure.test_reference_assignments import source
from tests.unit.figure.test_scheme_series import ROOT
from tests.unit.figure.test_structure_review import state as original_state

PRODUCT = "/pathways/0/steps/0/products/0"


def setup_state(site="SiteA"):
    state = original_state()
    state["extraction"]["status"] = "unclear"
    product = state["extraction"]["pathways"][0]["steps"][0]["products"][0]
    product.pop("markush")
    product.update(
        reference="family-Q",
        variants=[],
        compound_series={
            "members": ["lead-A", "lead-B"],
            "member_count": 2,
            "structure_status": "unresolved",
            "unresolved_reason": "No source assignments yet",
            "evidence_text": "Printed labels",
        },
    )
    state["exploration"]["evidence"] = [
        {
            "id": "proof",
            "tool": "inspect_visual",
            "result": {"node_ref": "d/r1/p007.g000", "image_refs": [{"blob_ref": "sample", "page": 7}]},
        }
    ]
    state["exploration"]["gaps"] = [
        {
            "gap_id": "members",
            "question": "Which assignments apply?",
            "field_paths": [PRODUCT + "/variants"],
            "status": "open",
        }
    ]
    args = {
        "product_path": PRODUCT,
        "evidence_ids": ["proof"],
        "relevance": "This source explicitly identifies members of the target family",
        "assignments": [
            {
                "reference": "lead-A",
                "substituents": {site: "Cl"},
                "attachments": [site],
                "evidence_text": "Verified source assignment",
            }
        ],
    }
    return state, args


def local_agent(schema=None):
    skill = SimpleNamespace(name="scheme_extract", output_schema=schema or load_package(ROOT).output_schema)

    async def list_visible():
        return [skill]

    async def ctx(state):
        return SimpleNamespace(registry=SimpleNamespace(list_visible=list_visible)), None

    async def source_review(state, arguments):
        return {"status": "passed", "issues": [], "summary": "Verified by test reviewer"}

    return SimpleNamespace(_ctx=ctx, _review_reference_assignments=source_review)


@pytest.mark.parametrize("site", ["SiteA", "NRaRb", "branch-k"])
async def test_preview_commit_and_repeat_are_transactional_for_arbitrary_attachment_names(site):
    state, args = setup_state(site)
    original = copy.deepcopy(state)
    preview = await edit_reference_assignments(local_agent(), source(), state, args, preview=True)
    assert preview["dry_run"] and preview["patch"]["changes"]
    assert state == original
    committed = await edit_reference_assignments(local_agent(), source(), state, args)
    assert state["exploration"]["extraction_version"] == 2
    assert committed["patch"]["changes"] == preview["patch"]["changes"]
    repeated = await edit_reference_assignments(local_agent(), source(), state, args)
    assert repeated["reused"] and not repeated["patch"]["changed"]
    assert state["exploration"]["extraction_version"] == 2
    assert len(state["exploration"]["reference_assignments"]) == 1
    targets = workflow_status(state)["member_targets"]
    assert targets[0]["missing_members"] == ["lead-B"]


async def test_apply_failure_does_not_leave_a_registered_source_without_a_committed_result():
    state, args = setup_state()
    original = copy.deepcopy(state)
    with pytest.raises(SchemaError):
        await edit_reference_assignments(
            local_agent({"required": ["missing-required-field"]}), source(), state, args
        )
    assert state == original


async def test_new_member_write_rejects_missing_attachment_before_mutation():
    from pydantic import ValidationError

    state, args = setup_state()
    args["assignments"][0].pop("attachments")
    before = copy.deepcopy(state)
    with pytest.raises(ValidationError, match="attachments"):
        await edit_reference_assignments(local_agent(), source(), state, args, preview=True)
    assert state == before


async def test_source_review_failure_cannot_commit_member_assignments():
    state, args = setup_state()
    agent = local_agent()

    async def rejected(s, a):
        raise EditRejected("Source row differs", {"code": "reference_assignment_review_failed"})

    agent._review_reference_assignments = rejected
    before = copy.deepcopy(state)
    with pytest.raises(EditRejected):
        await edit_reference_assignments(agent, source(), state, args)
    assert state == before


async def test_source_correction_is_local_audited_and_preserves_history():
    state, args = setup_state()
    args["assignments"].append({**copy.deepcopy(args["assignments"][0]), "reference": "lead-B"})
    await edit_reference_assignments(local_agent(), source(), state, args)
    revision = copy.deepcopy(args)
    revision.update(supersedes=["ra1"], correction_reason="A detailed source view corrects this member")
    revision["assignments"] = [revision["assignments"][0]]
    revision["assignments"][0]["substituents"]["SiteA"] = "Br"
    revision["assignments"][0]["evidence_text"] = "Corrected detailed source assignment"
    before = copy.deepcopy(state)
    preview = await edit_reference_assignments(local_agent(), source(), state, revision, preview=True)
    assert preview["dry_run"] and state == before
    rejecting = local_agent()

    async def reject(s, a):
        raise EditRejected("Source not clear", {"code": "reference_assignment_review_failed"})

    rejecting._review_reference_assignments = reject
    with pytest.raises(EditRejected):
        await edit_reference_assignments(rejecting, source(), state, revision)
    assert state == before
    result = await edit_reference_assignments(local_agent(), source(), state, revision)
    members = state["extraction"]["pathways"][0]["steps"][0]["products"][0]["variants"]
    assert {m["reference"]: m["substituents"]["SiteA"] for m in members} == {"lead-A": "Br", "lead-B": "Cl"}
    history = state["exploration"]["reference_assignment_revisions"]
    assert history[0]["assignments"][0]["substituents"] == {"SiteA": "Cl"}
    assert history[0]["assignments"][0]["evidence_text"] == "Verified source assignment"
    assert "Verified source assignment" not in members[0]["evidence_text"]
    assert result["patch"]["changes"]
    assert state["exploration"]["reference_assignment_errors"] == []
    again = await edit_reference_assignments(local_agent(), source(), state, revision)
    assert again["reused"] and not again["patch"]["changed"]
    assert len(state["exploration"]["reference_assignment_revisions"]) == 1


async def test_source_correction_rejects_unknown_supplement_without_mutation():
    state, args = setup_state()
    args.update(supersedes=["unknown"], correction_reason="Source correction")
    before = copy.deepcopy(state)
    with pytest.raises(ValueError, match="existing supplements"):
        await edit_reference_assignments(local_agent(), source(), state, args)
    assert state == before


async def test_source_assignment_does_not_overwrite_an_already_drawn_member():
    state, args = setup_state()
    product = state["extraction"]["pathways"][0]["steps"][0]["products"][0]
    product["variants"] = [
        {"reference": "lead-A", "mol_id": 17, "substituents": {}, "evidence_text": "Drawn member"}
    ]
    before = copy.deepcopy(state)
    with pytest.raises(EditRejected) as failure:
        await edit_reference_assignments(local_agent(), source(), state, args)
    assert failure.value.details["code"] == "direct_member_already_recognized"
    assert state == before


def test_series_can_combine_verified_template_assignments_with_a_drawn_member():
    from drugpaper.agents.figure.reference_assignments import (
        apply_reference_assignments,
        reconcile_drawn_source_records,
    )

    state, args = setup_state()
    product = state["extraction"]["pathways"][0]["steps"][0]["products"][0]
    product["variants"] = [
        {"reference": "lead-B", "mol_id": 17, "substituents": {}, "evidence_text": "Drawn member"}
    ]
    product["compound_series"]["structure_status"] = "partial"
    state["exploration"]["applied_evidence_ids"] = ["proof"]
    args["assignments"].append({**copy.deepcopy(args["assignments"][0]), "reference": "lead-B"})
    record_reference_assignments(source(), state, args)
    inventory = {
        "structures": [
            {"mol_id": product["scaffold_mol_id"], "recognized": True, "attachment_labels": ["SiteA"]},
            {"mol_id": 17, "recognized": True, "attachment_labels": []},
        ]
    }
    invalid = copy.deepcopy(inventory)
    invalid["structures"][1]["recognized"] = False
    assert (
        reconcile_drawn_source_records(state["extraction"], copy.deepcopy(state["exploration"]), invalid)
        == []
    )
    migrated = reconcile_drawn_source_records(state["extraction"], state["exploration"], inventory)
    assert [row["reference"] for row in migrated] == ["lead-B"]
    record = state["exploration"]["reference_assignments"][0]
    assert record["observations"][0]["assignment"]["substituents"] == {"SiteA": "Cl"}
    assert record["observations"][0]["recognized_mol_id"] == 17
    assert reconcile_drawn_source_records(state["extraction"], state["exploration"], inventory) == []
    assert len(record["observations"]) == 1
    output = apply_reference_assignments(state["extraction"], state["exploration"], inventory)
    updated = output["pathways"][0]["steps"][0]["products"][0]
    assert updated["compound_series"]["structure_status"] == "resolved"
    assert updated["variants"][0]["mol_id"] == 17 and not updated["variants"][0]["substituents"]
    assert state["exploration"]["reference_assignment_errors"] == []


def test_range_endpoint_feedback_explains_dependent_fields_together():
    from pydantic import ValidationError

    from drugpaper.facts.figure_models import MoleculeReference

    with pytest.raises(ValidationError) as failure:
        MoleculeReference(reference="71–72", scaffold_mol_id=8)
    feedback = recovery_error(failure.value)
    assert "compound_series" in feedback["repair"]["action"]
    assert "must coexist" in feedback["repair"]["action"]
    assert "without expanded cases" in feedback["repair"]["action"]


def test_schema_feedback_exposes_enum_values_at_exact_structure_path():
    from jsonschema import Draft202012Validator

    schema = {"type": "object", "properties": {"mode": {"enum": ["queued", "applied"]}}}
    error = next(Draft202012Validator(schema).iter_errors({"mode": "finished"}))
    output = recovery_error(error)
    assert output["repair"]["code"] == "structure_schema_violation"
    assert output["repair"]["fields"][0]["path"] == "/mode"
    assert output["repair"]["fields"][0]["allowed_values"] == ["queued", "applied"]


async def test_complete_registered_member_assignments_sync_series_without_claiming_assembly():
    state, args = setup_state()
    second = {**copy.deepcopy(args["assignments"][0]), "reference": "lead-B", "substituents": {"SiteA": "Me"}}
    args["assignments"].append(second)
    agent = local_agent()
    bundle, _ = await agent._ctx(state)

    async def context(s):
        return bundle, SimpleNamespace(
            materials=SimpleNamespace(
                target={
                    "children": [{"ref": "m", "attributes": {"reading_index": 1}}],
                    "structures": [{"node_ref": "m", "e_smiles_raw": "<a>1:SiteA</a>"}],
                }
            )
        )

    agent._ctx = context
    await edit_reference_assignments(agent, source(), state, args)
    product = state["extraction"]["pathways"][0]["steps"][0]["products"][0]
    assert product["compound_series"]["structure_status"] == "resolved"
    assert product["compound_series"]["unresolved_reason"] == ""
    assert all("assembled_molecule" not in member for member in product["variants"])


async def test_member_preview_defers_recognition_site_matching_until_backfill():
    agent = local_agent()
    state, args = setup_state("side")
    bundle, _ = await agent._ctx(state)

    async def ctx(s):
        return bundle, SimpleNamespace(
            materials=SimpleNamespace(
                target={
                    "children": [{"ref": "m", "attributes": {"reading_index": 1}}],
                    "structures": [{"node_ref": "m", "e_smiles_raw": "<a>1:side</a><a>2:ring</a>"}],
                }
            )
        )

    agent._ctx = ctx
    before = copy.deepcopy(state)
    result = await edit_reference_assignments(agent, source(), state, args, preview=True)
    assert result["ok"]
    assert state["extraction"] == before["extraction"]


async def test_position_column_is_not_silently_ignored_as_an_explanatory_substituent():
    state, args = setup_state()
    args["assignments"][0]["substituents"]["bridge position"] = "meta"
    before = copy.deepcopy(state)
    with pytest.raises(EditRejected) as failure:
        await edit_reference_assignments(local_agent(), source(), state, args, preview=True)
    assert failure.value.details["code"] == "misplaced_position_fields"
    assert failure.value.details["fields"] == ["bridge position"]
    assert state == before
    args["assignments"][0]["position_definitions"] = {"bridge position": "meta"}
    result = await edit_reference_assignments(local_agent(), source(), state, args, preview=True)
    assert result["dry_run"]


def test_input_correction_grace_is_bounded_and_not_semantic_progress():
    state, _ = setup_state()
    tracker = ProgressTracker(state, {})
    failure = {"ok": False, "repair": {"code": "invalid_arguments"}}
    for _ in range(2):
        tracker.idle = 6
        tracker.allow_correction("patch_structure", failure)
        assert tracker.idle == 0
        assert not tracker.observe(state, {})
    tracker.idle = 6
    tracker.allow_correction("patch_structure", failure)
    assert tracker.idle == 6


def test_split_patch_commits_only_clear_fully_satisfied_concrete_proposals():
    state, _ = setup_state()
    args = {
        "operations": [
            {"op": "replace", "path": PRODUCT + "/reference", "value": "new-family"},
            {"op": "replace", "path": "/status", "value": "ok"},
        ]
    }
    track_edit(state["exploration"], "patch_structure", args, {"ok": False})
    product = state["extraction"]["pathways"][0]["steps"][0]["products"][0]
    product["reference"] = "new-family"
    assert workflow_status(state)["pending_edits"]
    state["extraction"]["status"] = "ok"
    assert not workflow_status(state)["pending_edits"]
    assert state["exploration"]["pending_edits"][0]["status"] == "satisfied_by_current_state"
    track_edit(
        state["exploration"],
        "patch_structure",
        {"operations": [{"op": "replace", "value": 1}]},
        {"ok": False},
    )
    assert workflow_status(state)["pending_edits"]


def test_invalid_member_feedback_names_the_actual_missing_key_and_preserves_proposal():
    state, args = setup_state("NVar")
    args["assignments"][0]["substituents"] = {"Var": "Me"}
    before = copy.deepcopy(state)
    with pytest.raises(EditRejected) as failure:
        record_reference_assignments(source(), state, args)
    output = recovery_error(failure.value)
    assert output["repair"]["missing_keys"] == ["NVar"]
    assert output["repair"]["path"] == "/assignments/0/substituents"
    assert state == before
    track_edit(state["exploration"], "record_reference_assignments", args, output)
    edit = workflow_status(state)["pending_edits"][0]
    assert edit["arguments"] == args and edit["status"] == "needs_correction"


def test_gap_notes_and_repeated_validations_are_not_semantic_progress():
    state, _ = setup_state()
    tracker = ProgressTracker(state, {})
    for i in range(5):
        state["exploration"]["gaps"][0]["note"] = str(i)
        assert not tracker.observe(state, {})
    assert tracker.idle == 5
    tracker.finish_blocked_at = tracker.signature
    assert not tracker.can_finish()
    state["extraction"]["description"] = "Source-backed topology clarification"
    assert tracker.observe(state, {}) and tracker.idle == 0 and tracker.can_finish()


def test_valid_preview_is_progress_but_repeating_it_is_not():
    state, args = setup_state()
    tracker = ProgressTracker(state, {})
    track_edit(state["exploration"], "preview_reference_assignments", args, {"ok": True})
    assert tracker.observe(state, {})
    assert not tracker.observe(state, {})
    assert workflow_status(state)["pending_edits"][0]["status"] == "ready_to_apply"


def test_unrelated_patch_cannot_clear_a_failed_member_write():
    state, args = setup_state()
    track_edit(
        state["exploration"],
        "record_reference_assignments",
        args,
        {"ok": False, "repair": {"code": "missing_attachment_values"}},
    )
    track_edit(
        state["exploration"],
        "patch_structure",
        {"operations": [{"op": "replace", "path": PRODUCT + "/evidence_text", "value": "note"}]},
        {"ok": True},
    )
    assert workflow_status(state)["pending_edits"]
    state["extraction"]["pathways"][0]["steps"][0]["products"][0]["variants"] = [
        {**args["assignments"][0], "scaffold_mol_id": 1}
    ]
    assert not workflow_status(state)["pending_edits"]


async def test_resume_loads_real_pixels_with_priority_and_explicit_omissions(tmp_path):
    store = LocalFsBlobStore(tmp_path)
    first = await store.put(b"first-image", "image/png")
    important = await store.put(b"important-image", "image/png")
    log = {
        "evidence": [
            {
                "id": "other",
                "tool": "inspect_visual",
                "result": {"node_ref": "d/r1/p090.g002", "image_refs": [{"blob_ref": first, "page": 90}]},
            },
            {
                "id": "needed",
                "tool": "inspect_visual",
                "result": {"node_ref": "d/r1/p012.g007", "image_refs": [{"blob_ref": important, "page": 12}]},
            },
        ]
    }
    track_edit(
        log,
        "record_reference_assignments",
        {"product_path": PRODUCT, "evidence_ids": ["needed"], "assignments": []},
        {"ok": False},
    )
    messages, event = await restore_evidence(store, log, "d/r1/p000.g000", 1, 1000)
    assert len(messages) == 1 and event["image_refs"][0]["blob_ref"] == important
    assert event["omitted_evidence_ids"] == ["other"]
    assert messages[0].content[1].data_b64
    assert not log["pending_edits"][0].get("applied")


def test_reference_batches_do_not_consume_local_topology_patch_budget():
    assert (
        local_patch_count({"structure_patches": [{"kind": "reference_member_fields"}] * 12 + [{}, {}]}) == 2
    )
    log = {"patch_budget_baseline": 2, "structure_patches": [{}, {}, {"kind": "reference_member_fields"}, {}]}
    assert local_patch_count(log) == 1
    assert len(log["structure_patches"]) == 4


def test_explicit_deferral_preserves_proposal_and_requires_a_correction_attempt():
    from drugpaper.agents.figure.workflow import defer_edit

    state, args = setup_state()
    log = state["exploration"]
    track_edit(log, "record_reference_assignments", args, {"ok": False})
    disposition = {
        "edit_id": "edit1",
        "disposition": "needs_evidence",
        "reason": "The cited source does not establish the attachment position",
        "evidence_ids": ["proof"],
    }
    with pytest.raises(ValueError, match="Try correcting"):
        defer_edit(log, disposition)
    track_edit(log, "record_reference_assignments", args, {"ok": False})
    before = copy.deepcopy(state["extraction"])
    defer_edit(log, disposition)
    board = workflow_status(state)
    assert not board["pending_edits"]
    assert board["deferred_edits"][0]["arguments"] == args
    assert state["extraction"] == before


async def test_resume_never_attaches_a_different_document_reference(tmp_path):
    store = LocalFsBlobStore(tmp_path)
    blob = await store.put(b"foreign-image", "image/png")
    messages, event = await restore_evidence(
        store,
        {
            "evidence": [
                {
                    "id": "foreign",
                    "tool": "inspect_visual",
                    "result": {"node_ref": "other-doc/r1/p000.g000", "image_refs": [{"blob_ref": blob}]},
                }
            ]
        },
        "owned-doc/r1/p000.g000",
        4,
        1000,
    )
    assert not messages and event["unavailable_evidence_ids"] == ["foreign"]


async def test_nonprogressing_gap_updates_cannot_keep_an_agent_alive(figure_env):
    from drugpaper.agents.figure.exploration import explore
    from tests.unit.figure.test_exploration import harness

    calls = [("get_node", {"node_id": "p000.g000"}), ("extract_figure", {})]
    calls.extend(
        (
            "update_gap",
            {
                "gap_id": "open-question",
                "question": "Unresolved observation",
                "field_paths": ["/status"],
                "note": str(i),
            },
        )
        for i in range(12)
    )
    agent, state, _ = await harness(figure_env, calls)
    agent.services.settings.figure_exploration_max_stagnant_calls = 4
    await explore(agent, state)
    assert state["exploration"]["stop_reason"] == "no_progress"
    assert state["exploration"]["tool_calls"] == 6
    assert state["exploration"]["unfinished_work"]["mode"] == "close_pending_work"


async def test_failed_edit_preview_and_commit_recover_without_reextracting(figure_env):
    from types import MethodType

    from drugpaper.agents.figure.exploration import explore
    from tests.unit.figure.test_exploration import harness
    from tests.unit.figure.test_structure_review import CASE_PATH, arguments, audit_payload, extraction

    bad = arguments()
    bad["operations"][0]["op"] = "replace"  # A /- path is append-only, not replacement.
    calls = [
        ("inspect_visual", {"node_id": "doc_chem/r1/p000.g000"}),
        ("extract_figure", {}),
        ("patch_structure", bad),
        ("get_structure", {"path": CASE_PATH}),
        ("preview_structure_patch", arguments()),
        ("patch_structure", arguments()),
        ("review_structure", {}),
        ("validate_result", {}),
    ]
    agent, state, model = await harness(figure_env, calls)
    state["chain"] = ["scheme_extract"]
    agent.services.settings.figure_exploration_max_turns = 8
    extracts, reviews = [], []

    async def extract(self, s):
        extracts.append(True)
        s["extraction"] = extraction()

    async def review(self, s, name, inputs, **kwargs):
        reviews.append(True)
        return audit_payload(failed=len(reviews) == 1)

    agent._structure_once = MethodType(extract, agent)
    agent._run_skill = MethodType(review, agent)
    await explore(agent, state)
    assert len(extracts) == 1 and len(reviews) == 2
    assert state["exploration"]["extraction_version"] == 2
    assert not workflow_status(state)["pending_edits"]
    assert "finish" not in {t.name for t in model.requests[3].tools}
    assert "ready_to_apply" in model.requests[5].messages[-1].text()


async def test_explicit_withdrawal_keeps_required_reason_in_real_dispatch(figure_env):
    from types import MethodType

    from drugpaper.agents.figure.exploration import explore
    from tests.unit.figure.test_exploration import harness
    from tests.unit.figure.test_structure_review import arguments, audit_payload, extraction

    bad = arguments()
    bad["operations"][0]["op"] = "replace"
    reason = "Withdraw an invalid append proposal; the original extraction is retained"
    calls = [
        ("inspect_visual", {"node_id": "doc_chem/r1/p000.g000"}),
        ("extract_figure", {}),
        ("patch_structure", bad),
        ("defer_edit", {"edit_id": "edit1", "disposition": "withdrawn", "reason": reason}),
    ]
    agent, state, _ = await harness(figure_env, calls)
    state["chain"] = ["scheme_extract"]
    agent.services.settings.figure_exploration_max_turns = 4

    async def extract(self, s):
        s["extraction"] = extraction()

    async def review(self, s, name, inputs, **kwargs):
        return audit_payload(failed=True)

    agent._structure_once = MethodType(extract, agent)
    agent._run_skill = MethodType(review, agent)
    await explore(agent, state)
    edit = state["exploration"]["pending_edits"][0]
    assert edit["status"] == "withdrawn"
    assert edit["disposition_reason"] == reason


def test_evidence_backed_gap_closure_is_finite_progress():
    state, _ = setup_state()
    tracker = ProgressTracker(state, {})
    gap = state["exploration"]["gaps"][0]
    gap.update(status="unresolved", evidence_ids=["proof"], note="Actual source leaves this ambiguous")
    assert tracker.observe(state, {})
    gap["note"] = "Rewording does not add progress"
    assert not tracker.observe(state, {})
    gap["status"] = "open"
    assert not tracker.observe(state, {})
    gap["status"] = "unresolved"
    assert not tracker.observe(state, {})


def test_handoff_preserves_unknown_candidates_but_never_closes_actual_member_errors():
    from drugpaper.agents.figure.workflow import ready_for_handoff

    state, _ = setup_state()
    gap = state["exploration"]["gaps"][0]
    gap.update(status="unresolved", evidence_ids=["proof"], note="Source leaves the symbolic range open")
    validation = {
        "status": "unclear",
        "checks": [{"severity": "error", "code": "C_BACKFILL_ERR"}],
        "backfill_errors": [{"code": "markush_candidate_unresolved", "field_path": PRODUCT + "/variants"}],
    }

    def ready(**kwargs):
        return ready_for_handoff(
            state, validation, validated=kwargs.get("validated", True), reviewed=kwargs.get("reviewed", True)
        )

    assert ready()
    assert not ready(validated=False)
    assert not ready(reviewed=False)
    validation["backfill_errors"][0]["code"] = "compound_instance_failed"
    assert not ready()
    validation["backfill_errors"][0]["code"] = "markush_candidate_unresolved"
    gap["status"] = "open"
    assert not ready()
    gap["status"] = "unresolved"
    gap["evidence_ids"] = ["fabricated"]
    assert not ready()
    gap["evidence_ids"] = ["proof"]
    track_edit(state["exploration"], "patch_structure", {"operations": []}, {"ok": False})
    assert not ready()


async def test_reviewed_validated_resume_requests_finish_without_redundant_tools(figure_env):
    from types import MethodType

    from drugpaper.agents.figure.exploration import explore
    from tests.unit.figure.test_exploration import FINISH, harness
    from tests.unit.figure.test_structure_review import audit_payload, case, extraction

    agent, state, model = await harness(figure_env, [("validate_result", {}), FINISH])
    raw = extraction()
    raw["pathways"][0]["steps"][0]["products"][0]["markush"]["substitution_cases"].append(case("Cl"))
    state.update(chain=["scheme_extract"], extraction=raw, resume_structure=True)
    state["exploration"] = {
        "extraction_version": 4,
        "evidence": [{"id": "e1", "tool": "inspect_visual", "result": {"node_ref": state["node_ref"]}}],
        "applied_evidence_ids": ["e1"],
        "gaps": [],
    }

    async def skill(self, s, name, inputs, **kwargs):
        assert name == "structure_audit"
        return audit_payload()

    agent._run_skill = MethodType(skill, agent)
    await explore(agent, state)
    assert state["exploration"]["stop_reason"] == "agent_finished"
    assert {t.name for t in model.requests[0].tools} == {"validate_result"}
    assert {t.name for t in model.requests[1].tools} == {"finish"}
    assert "submit_reviewed_result" in model.requests[1].messages[-1].text()
