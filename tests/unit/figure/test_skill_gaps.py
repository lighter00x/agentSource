"""A Skill reports actionable gaps; each subject needs its own disposition."""

import copy
import json
import re
from types import MethodType

import pytest
from jsonschema import Draft202012Validator
from pydantic import ValidationError

from drugpaper.agents.figure.context import import_skill_gaps, uninvestigated_skill_gaps, update_gap
from drugpaper.facts.figure_models import VisualExtraction
from drugpaper.skills.loader import load_package
from drugpaper.skills.registry import BUILTIN_ROOT


def report(key="assignments", path="/members", category="missing_evidence"):
    return {
        "gap_id": key,
        "question": f"Confirm {key}",
        "field_paths": [path],
        "category": category,
        "next_action": "search_text",
        "query_terms": [key],
    }


def ledger():
    return {
        "gaps": [],
        "evidence": [{"id": "e1", "tool": "get_page_text"}],
        "applied_evidence_ids": ["e1"],
        "extraction_version": 1,
    }


def disposition(gap, **kwargs):
    return {
        k: copy.deepcopy(v)
        for k, v in {**gap, **kwargs}.items()
        if k in {"gap_id", "question", "field_paths", "status", "note", "evidence_ids"}
    }


def test_scheme_prompt_example_is_valid_against_wire_and_semantics():
    skill = load_package(BUILTIN_ROOT / "scheme_extract")
    assert skill.ok, skill.errors
    examples = re.findall(r"```json\n(.*?)\n```", skill.prompts["main"], re.S)
    assert examples
    for text in examples:
        payload = json.loads(text)
        Draft202012Validator(skill.output_schema).validate(payload)
        parsed = VisualExtraction.model_validate(payload)
        assert parsed.information_gaps
        assert parsed.pathways[0].steps[0].products[0].variants == []


@pytest.mark.parametrize("path", ["/members", "/pathways/0/steps/0/conditions", "/axis/unit"])
def test_common_gap_contract_does_not_depend_on_chemical_number_ranges(path):
    parsed = VisualExtraction.model_validate({"status": "unclear", "information_gaps": [report(path=path)]})
    assert parsed.information_gaps[0].field_paths == [path]


@pytest.mark.parametrize("mistake", ["false_ok", "duplicate", "wildcard", "unknown_category"])
def test_gap_contract_rejects_unusable_reports(mistake):
    payload = {"status": "unclear", "information_gaps": [report()]}
    if mistake == "false_ok":
        payload["status"] = "ok"
    elif mistake == "duplicate":
        payload["information_gaps"].append(report())
    elif mistake == "wildcard":
        payload["information_gaps"][0]["field_paths"] = ["/pathways/*/steps"]
    else:
        payload["information_gaps"][0]["category"] = "absent_from_entire_paper"
    with pytest.raises(ValidationError):
        VisualExtraction.model_validate(payload)


def test_one_disposition_cannot_close_other_subject_and_omission_is_not_resolution():
    log = ledger()
    payload = {"information_gaps": [report("members"), report("conditions", "/conditions")]}
    import_skill_gaps(log, payload)
    first = log["gaps"][0]
    update_gap(
        log,
        disposition(first, status="unresolved", evidence_ids=["e1"], note="Page lacks assignments"),
        payload,
        True,
        {},
    )
    assert uninvestigated_skill_gaps(log) == ["skill:conditions"]
    import_skill_gaps(log, {})
    assert len(log["gaps"]) == 2
    assert uninvestigated_skill_gaps(log) == ["skill:conditions"]


def test_unread_evidence_and_preview_do_not_authorize_finish():
    log = ledger()
    import_skill_gaps(log, {"information_gaps": [report()]})
    gap = log["gaps"][0]
    with pytest.raises(ValueError, match="actual text/high-resolution"):
        update_gap(log, disposition(gap, evidence_ids=["invented"]), {}, False, {})
    log["evidence"].append({"id": "preview", "tool": "preview_pages"})
    with pytest.raises(ValueError, match="previews"):
        update_gap(log, disposition(gap, evidence_ids=["preview"]), {}, False, {})
    update_gap(
        log,
        disposition(gap, status="unresolved", evidence_ids=["e1"], note="Read but not applied"),
        {},
        False,
        {},
    )
    log["applied_evidence_ids"] = []
    assert uninvestigated_skill_gaps(log) == ["skill:assignments"]


def test_capability_limit_has_explicit_disposition_without_fruitless_search():
    log = ledger()
    import_skill_gaps(log, {"information_gaps": [report(category="unsupported_representation")]})
    gap = log["gaps"][0]
    assert uninvestigated_skill_gaps(log)
    update_gap(
        log,
        disposition(gap, status="unresolved", note="Detected scaffold has no matching attachment"),
        {},
        False,
        {},
    )
    assert uninvestigated_skill_gaps(log) == []


def test_answer_can_be_validated_without_other_subjects_being_complete():
    log = ledger()
    import_skill_gaps(log, {"members": [], "information_gaps": [report()]})
    gap = log["gaps"][0]
    update_gap(
        log,
        disposition(gap, status="resolved", evidence_ids=["e1"], note="Members read from the cited row"),
        {"members": ["A"], "information_gaps": [report("condition", "/conditions")]},
        True,
        {"status": "unclear", "checks": [{"severity": "error", "field_path": "$.conditions"}]},
    )
    assert gap["status"] == "resolved"
    assert gap["changes"]["/members"]["after"] == ["A"]


def test_still_reported_or_affected_validation_error_cannot_be_closed():
    log = ledger()
    payload = {"members": [], "information_gaps": [report()]}
    import_skill_gaps(log, payload)
    args = disposition(log["gaps"][0], status="resolved", evidence_ids=["e1"], note="Claimed done")
    with pytest.raises(ValueError, match="still reports"):
        update_gap(log, args, payload, True, {})
    with pytest.raises(ValueError, match="still have validation errors"):
        update_gap(
            log,
            args,
            {"members": ["A"]},
            True,
            {"checks": [{"severity": "error", "field_path": "$.members[0]"}]},
        )


def test_validated_answer_retires_only_its_report_without_consuming_structure_review():
    from drugpaper.agents.figure.structure_review import audit_passed, fingerprint

    log = ledger()
    payload = {"members": [], "information_gaps": [report(), report("other", "/conditions")]}
    import_skill_gaps(log, payload)
    payload["members"] = ["A"]
    audit = {"status": "passed", "extraction_hash": fingerprint(payload)}
    log.update(structure_audit=audit, structure_reviews=[audit])
    original_hash = audit["extraction_hash"]
    update_gap(
        log,
        disposition(log["gaps"][0], status="resolved", evidence_ids=["e1"], note="Cited row supplies A"),
        payload,
        True,
        {"status": "unclear", "checks": [{"severity": "error", "field_path": "$.conditions"}]},
    )
    assert [g["gap_id"] for g in payload["information_gaps"]] == ["other"]
    assert log["gaps"][0]["status"] == "resolved"
    assert log["gaps"][1]["status"] == "open"
    assert log["gap_resolutions"][0]["reports"][0]["gap_id"] == "assignments"
    assert log["extraction_version"] == 1
    assert audit_passed({"exploration": log, "extraction": payload})
    assert log["structure_reviews"] == [{"status": "passed", "extraction_hash": original_hash}]


def test_rejected_closure_preserves_report_and_explains_actual_next_action():
    from drugpaper.agents.figure.workflow import recovery_error

    log = ledger()
    payload = {"members": [], "information_gaps": [report()]}
    import_skill_gaps(log, payload)
    payload["members"] = ["A"]
    args = disposition(log["gaps"][0], status="resolved", evidence_ids=["e1"], note="Cited row supplies A")
    with pytest.raises(ValueError) as exc:
        update_gap(log, args, payload, False, {})
    feedback = recovery_error(exc.value)
    assert feedback["repair"]["code"] == "gap_validation_required"
    assert feedback["repair"]["gap_id"] == "skill:assignments"
    assert "validate_result" in feedback["repair"]["action"]
    assert len(payload["information_gaps"]) == 1
    assert "gap_resolutions" not in log
    with pytest.raises(ValueError, match="still have validation errors"):
        update_gap(log, args, payload, True, {"backfill_errors": [{"field_path": "$.members[0]"}]})
    assert len(payload["information_gaps"]) == 1
    assert log["gaps"][0]["status"] == "open"


async def test_skill_gaps_are_automatically_registered_and_finish_checks_each_subject(figure_env):
    from drugpaper.agents.figure.exploration import explore
    from tests.unit.figure.test_exploration import FINISH, harness

    payload = {
        "status": "unclear",
        "information_gaps": [report("members"), report("conditions", "/conditions")],
    }
    base = {
        "status": "unresolved",
        "evidence_ids": ["e1"],
        "note": "Read page; this subject remains undefined",
    }
    agent, state, _ = await harness(
        figure_env,
        [
            ("get_page_text", {"page": 0}),
            ("extract_figure", {}),
            ("validate_result", {}),
            (
                "update_gap",
                {
                    "gap_id": "skill:members",
                    "question": "Confirm members",
                    "field_paths": ["/members"],
                    **base,
                },
            ),
            FINISH,
            (
                "update_gap",
                {
                    "gap_id": "skill:conditions",
                    "question": "Confirm conditions",
                    "field_paths": ["/conditions"],
                    **base,
                },
            ),
            FINISH,
        ],
    )

    async def extract(self, state):
        state["extraction"] = copy.deepcopy(payload)
        return state

    agent._structure_once = MethodType(extract, agent)
    await explore(agent, state)
    log = state["exploration"]
    results = [s["result"] for s in log["steps"] if s.get("name") == "finish" and s["kind"] == "tool"]
    assert not results[0]["ok"] and "skill:conditions" in results[0]["error"]
    assert results[1]["ok"]
    assert log["status"] == "needs_review"
    assert len(log["gaps"]) == 2


def test_evidence_can_confirm_existing_value_without_fabricating_field_changes():
    log = ledger()
    import_skill_gaps(log, {"members": ["A"], "information_gaps": [report()]})
    gap = log["gaps"][0]
    update_gap(
        log,
        disposition(
            gap, status="resolved", evidence_ids=["e1"], note="Source explicitly confirms the original label"
        ),
        {"members": ["A"], "information_gaps": []},
        True,
        {"status": "unclear", "checks": []},
    )
    assert gap["status"] == "resolved" and gap["changes"] == {}


def test_prefixed_skill_id_fits_agent_gap_limit():
    log = ledger()
    import_skill_gaps(log, {"information_gaps": [report("g" * 74)]})
    assert len(log["gaps"][0]["gap_id"]) == 80


def test_new_evidence_need_cannot_reuse_old_capability_exemption():
    log = ledger()
    import_skill_gaps(log, {"information_gaps": [report(category="unsupported_representation")]})
    gap = log["gaps"][0]
    update_gap(log, disposition(gap, status="unresolved", note="Representation limitation"), {}, False, {})
    assert not uninvestigated_skill_gaps(log)
    import_skill_gaps(log, {"information_gaps": [report(category="missing_evidence")]})
    assert uninvestigated_skill_gaps(log) == ["skill:assignments"]
