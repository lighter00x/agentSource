import copy

import pytest

from drugpaper.agents.figure.investigation import investigation_plan, require_investigation
from tests.unit.figure.test_reference_assignments import PATH, RELATED, TARGET, source, state


def pending_state():
    s = state()
    s["exploration"]["evidence"] = [s["exploration"]["evidence"][0]]
    s["exploration"]["gaps"] = [
        {
            "gap_id": "members",
            "question": "Which groups define members 73–74?",
            "field_paths": [PATH + "/variants"],
            "status": "unresolved",
            "query_terms": ["73", "74"],
            "note": "Missing mappings",
        }
    ]
    return s


def test_two_partial_sources_do_not_replace_unread_complete_member_source():
    src = source()
    src.node(RELATED).text_norm = "Preparation of compound 73"
    src.node("d/r1/p009.g000").text_norm = "Assignments for compounds 73–74"
    extra = src.node(RELATED).model_copy(
        deep=True,
        update={"local_id": "p008.g000", "label": "Scheme 13", "text_norm": "Preparation of compound 74"},
    )
    src.ir.nodes.append(extra)
    src._by_id[extra.local_id] = extra
    s = pending_state()
    s["exploration"]["evidence"].extend(
        [
            {"tool": "inspect_visual", "result": {"node_ref": RELATED}},
            {"tool": "inspect_visual", "result": {"node_ref": "d/r1/p008.g000"}},
        ]
    )
    q = investigation_plan(src, s, {})["questions"][0]
    assert q["additional_source_reads_required"] == 1
    assert q["unread_complete_member_sources"] == ["d/r1/p009.g000"]


def test_known_body_citation_resolves_to_real_source_even_without_matching_caption_members():
    s = pending_state()
    s["exploration"]["evidence"].append(
        {
            "id": "text",
            "tool": "get_page_text",
            "result": {
                "data": {
                    "blocks": [{"text": "Compound 73 has the methyl group. See Table 5 for assignments."}]
                },
            },
        }
    )
    plan = investigation_plan(source(), s, {})["questions"][0]
    assert any(c["node_id"] == "d/r1/p009.g000" and c["cited_in_read_text"] for c in plan["candidates"])
    assert plan["additional_source_reads_required"] == 2
    with pytest.raises(ValueError, match="unexamined document evidence"):
        require_investigation(source(), s, {})


def test_current_image_text_disposition_and_preview_do_not_replace_source_read():
    s = pending_state()
    s["exploration"]["gaps"][0].update(evidence_ids=["e1"], note="Unsupported; stop")
    s["exploration"]["evidence"].append(
        {
            "tool": "preview_pages",
            "result": {"image_refs": [{"page": 7}]},
        }
    )
    assert investigation_plan(source(), s, {})["questions"][0]["additional_source_reads_required"] == 1
    with pytest.raises(ValueError):
        require_investigation(source(), s, {})
    s["exploration"]["evidence"].append({"tool": "inspect_visual", "result": {"node_ref": RELATED}})
    require_investigation(source(), s, {})


def test_each_question_needs_its_own_sources():
    s = pending_state()
    other = copy.deepcopy(s["exploration"]["gaps"][0])
    other.update(
        gap_id="other", question="Which groups define 85–89?", field_paths=["/unknown"], query_terms=[]
    )
    s["exploration"]["gaps"].append(other)
    s["exploration"]["evidence"].append({"tool": "inspect_visual", "result": {"node_ref": RELATED}})
    q = investigation_plan(source(), s, {})["questions"]
    assert q[0]["additional_source_reads_required"] == 0
    assert q[1]["additional_source_reads_required"] == 1


def test_open_abbreviation_error_becomes_an_evidence_question_and_is_deduplicated():
    s = pending_state()
    s["exploration"]["gaps"] = []
    error = {
        "field_path": "$.pathways[0].steps[0].products[0]",
        "reason": "unbounded_Group_requires_explicit_definition",
    }
    q = investigation_plan(source(), s, {"backfill_errors": [error, error]})["questions"]
    assert len(q) == 1
    assert q[0]["candidates"][0]["node_id"] == RELATED
    with pytest.raises(ValueError):
        require_investigation(source(), s, {"backfill_errors": [error]})


def test_query_or_reason_is_not_a_source_citation_and_no_cross_document_nodes():
    s = pending_state()
    s["exploration"]["evidence"].append(
        {
            "tool": "search",
            "arguments": {"query": "Table 5"},
            "reason": "Read Table 5 for compound 73",
            "result": {"matches": []},
        }
    )
    q = investigation_plan(source(), s, {})["questions"][0]
    assert [c["node_id"] for c in q["candidates"]] == [RELATED]
    assert TARGET not in [c["node_id"] for c in q["candidates"]]


def test_whole_page_can_cover_source_but_custom_crop_cannot():
    s = pending_state()
    s["exploration"]["evidence"].append(
        {
            "tool": "inspect_page",
            "arguments": {"bbox": [0, 0, 0.5, 0.5]},
            "result": {"image_refs": [{"page": 7}]},
        }
    )
    with pytest.raises(ValueError):
        require_investigation(source(), s, {})
    s["exploration"]["evidence"][-1]["arguments"].pop("bbox")
    require_investigation(source(), s, {})
