"""Coarse-to-fine navigation, bounded multimodal history and evidence application."""

import copy

import pytest

from drugpaper.agents.figure.context import compact_context, document_map, extraction_evidence, update_gap
from drugpaper.docsrc.ir_source import IrDocumentSource
from drugpaper.domain.geometry import BBox, Geometry, Region
from drugpaper.domain.nodes import DocumentNode, IRDocument, PageInfo
from drugpaper.gateway.base import ImagePart, Message, TextPart, ToolCall


def test_structuring_view_removes_computed_molecules_without_changing_source_or_ids():
    from drugpaper.agents.figure.context import structuring_view

    original = {
        "mol_id": 3,
        "text": "Paper reports R=Me and C2H6",
        "substituents": {"R": "Me"},
        "nested": [{"canonical_smiles": "PRIVATE", "formula": "PRIVATE_FORMULA", "reference": "23"}],
        "structures": [{"e_smiles_raw": "PRIVATE"}],
        "scaffold_esmiles": "PRIVATE_SCAFFOLD",
        "backfilled_substituents": {"R": "PRIVATE_FRAGMENT"},
        "attachment_labels": ["R", "Y"],
    }
    before = copy.deepcopy(original)
    visible = structuring_view(original)
    assert visible == {
        "mol_id": 3,
        "text": "Paper reports R=Me and C2H6",
        "substituents": {"R": "Me"},
        "nested": [{"reference": "23"}],
    }
    assert original == before


def image():
    return ImagePart(data_b64="QUJD" * 100)


def test_compaction_preserves_target_tool_pairs_reasoning_and_original_history():
    messages = [
        Message(role="system", content="task"),
        Message(role="user", content=[TextPart(text="target"), image()]),
        Message(
            role="assistant",
            tool_calls=[ToolCall(id="c1", name="preview_pages", arguments={"pages": [0]})],
            response_items=[{"type": "reasoning", "id": "rs1"}],
        ),
        Message(role="tool", name="preview_pages", tool_call_id="c1", content="e1"),
        Message(role="user", content=[TextPart(text="preview e1"), image(), image()]),
        Message(role="user", content=[TextPart(text="HD e2"), image()]),
    ]
    before = copy.deepcopy(messages)
    bounded, usage = compact_context(messages, 2, 100_000)
    assert usage["active_images"] == 2
    assert usage["released_images"] == 2
    assert bounded[2].tool_calls == messages[2].tool_calls
    assert bounded[2].response_items == messages[2].response_items
    assert bounded[3].tool_call_id == "c1"
    assert isinstance(bounded[1].content[1], ImagePart)
    assert isinstance(bounded[-1].content[1], ImagePart)
    assert messages == before


def test_target_is_not_silently_truncated_for_image_or_byte_limit():
    messages = [Message(role="system", content="task"), Message(role="user", content=[image(), image()])]
    with pytest.raises(ValueError, match="Target segments"):
        compact_context(messages, 1, 100_000)
    with pytest.raises(ValueError, match="byte budget"):
        compact_context(messages, 2, 100)


def test_document_map_discovers_far_page_and_crosspage_node_after_first_100_visuals():
    nodes = [
        DocumentNode(
            document_id="d",
            revision=1,
            local_id=f"p{i:03}.g000",
            node_type="table",
            label=f"Table {i}",
            geometry=Geometry(regions=[Region(page=i, bbox=BBox(x0=0, y0=0, x1=1, y1=1))]),
        )
        for i in range(105)
    ]
    nodes[-1].geometry.regions.append(Region(page=105, bbox=BBox(x0=0, y0=0, x1=1, y1=1)))
    source = IrDocumentSource(
        IRDocument(
            document_id="d",
            pages=[PageInfo(page=i, width_pt=100, height_pt=100) for i in range(106)],
            nodes=nodes,
        )
    )
    result = document_map(source, 104, 2)
    assert result["next_offset"] is None
    assert result["pages"][0]["visuals"][0]["node_id"] == "d/r1/p104.g000"
    assert result["pages"][1]["visuals"][0]["pages"] == [104, 105]
    assert result["pages"][0]["display_page"] == 105


def log_and_gap():
    log = {
        "gaps": [],
        "evidence": [
            {"id": "e1", "tool": "inspect_visual", "result": {}},
            {"id": "e2", "tool": "preview_pages", "result": {}},
        ],
        "applied_evidence_ids": ["e1"],
        "extraction_version": 1,
    }
    args = {"gap_id": "members", "question": "编号对应什么取代基？", "field_paths": ["/members"]}
    update_gap(log, args, {"members": []}, False, {})
    return log, args


def test_gap_resolution_requires_actual_applied_evidence_and_changed_field():
    log, args = log_and_gap()
    closed = {**args, "status": "resolved", "evidence_ids": ["e1"], "note": "Table 1 给出了编号与取代基"}
    with pytest.raises(ValueError, match="actually change"):
        update_gap(log, closed, {"members": []}, True, {})
    with pytest.raises(ValueError, match="then validate_result"):
        update_gap(log, closed, {"members": ["1"]}, False, {})
    result = update_gap(log, closed, {"members": ["1"]}, True, {"status": "completed"})
    assert result["gap"]["changes"]["/members"] == {"before": [], "after": ["1"]}


def test_aggregate_backfill_check_does_not_block_an_unrelated_verified_skill_gap():
    log, args = log_and_gap()
    log["gaps"][0]["source"] = "skill"
    close = {
        "gap_id": "members",
        "status": "resolved",
        "evidence_ids": ["e1"],
        "note": "Actual source applied",
    }
    validation = {
        "status": "unclear",
        "checks": [{"code": "C_BACKFILL_ERR", "severity": "error", "field_path": ""}],
        "backfill_errors": [{"code": "compound_instance_failed", "field_path": "$.other[0]"}],
    }
    result = update_gap(log, close, {"members": ["verified"]}, True, validation)
    assert result["gap"]["status"] == "resolved"
    validation["backfill_errors"][0]["field_path"] = "$.members[0]"
    with pytest.raises(ValueError, match="Affected fields"):
        update_gap(log, close, {"members": ["verified"]}, True, validation)
    validation["backfill_errors"][0]["field_path"] = ""
    with pytest.raises(ValueError, match="Unlocated"):
        update_gap(log, close, {"members": ["verified"]}, True, validation)


@pytest.mark.parametrize("case", ["preview", "unapplied", "validation_error", "incomplete"])
def test_gap_cannot_be_closed_with_insufficient_evidence(case):
    log, args = log_and_gap()
    validation = {}
    closed = {**args, "status": "resolved", "evidence_ids": ["e1"], "note": "证据核对"}
    if case == "preview":
        closed["evidence_ids"] = ["e2"]
    elif case == "unapplied":
        log["applied_evidence_ids"] = []
    elif case == "validation_error":
        validation = {"checks": [{"severity": "error"}]}
    else:
        validation = {"status": "unclear"}
    with pytest.raises(ValueError):
        update_gap(log, closed, {"members": ["1"]}, True, validation)
    assert log["gaps"][0]["status"] == "open"


def test_previews_and_explicitly_excluded_evidence_do_not_enter_extraction():
    log, _ = log_and_gap()
    assert [e["id"] for e in extraction_evidence(log)] == ["e1"]
    log["selected_evidence_ids"] = []
    assert extraction_evidence(log) == []


def test_structuring_request_budget_handles_schema_class_and_rejects_excess_images():
    from types import SimpleNamespace

    from pydantic import BaseModel

    from drugpaper.gateway.base import ChatRequest
    from drugpaper.skills.prompt_materials import check_evidence_request

    class Output(BaseModel):
        value: str

    ctx = SimpleNamespace(extras={"figure_context_limits": {"images": 2, "bytes": 100_000}})
    request = ChatRequest(messages=[Message(role="user", content=[image(), image()])], response_schema=Output)
    assert check_evidence_request(request, ctx) is request
    request.messages[0].content.append(image())
    with pytest.raises(ValueError, match="select_context"):
        check_evidence_request(request, ctx)


def test_conflicting_sources_remain_recorded_and_cannot_redefine_gap_to_close_it():
    log, args = log_and_gap()
    result = update_gap(
        log,
        {**args, "status": "conflict", "evidence_ids": ["e1"], "note": "图中位置与表格不同"},
        {},
        False,
        {},
    )
    assert result["gap"]["status"] == "conflict"
    with pytest.raises(ValueError, match="original question"):
        update_gap(log, {**args, "question": "换成容易解决的问题"}, {}, False, {})


def test_validator_jsonpath_is_normalized_for_gap_tracking():
    log = {"gaps": [], "evidence": []}
    result = update_gap(
        log,
        {
            "gap_id": "mapping",
            "question": "编号映射",
            "field_paths": ["$.pathways[0].steps[2].products[0].compound_series"],
        },
        {},
        False,
        {},
    )
    assert result["gap"]["field_paths"] == ["/pathways/0/steps/2/products/0/compound_series"]
    update_gap(
        log,
        {
            "gap_id": "mapping",
            "question": "编号映射",
            "field_paths": ["/pathways/0/steps/2/products/0/compound_series"],
        },
        {},
        False,
        {},
    )
    assert len(log["gaps"]) == 1


@pytest.mark.parametrize("path", ["$", "$.pathways[*]", "$.pathways[-1]", "$.pathways[__import__('os')]", ""])
def test_gap_paths_never_evaluate_expressions(path):
    with pytest.raises(ValueError):
        update_gap(
            {"gaps": [], "evidence": []},
            {"gap_id": "g", "question": "q", "field_paths": [path]},
            {},
            False,
            {},
        )


def test_current_structure_inventory_only_exposes_owned_ids_and_connection_labels():
    from types import SimpleNamespace

    from drugpaper.agents.figure.context import current_structure_inventory

    material = SimpleNamespace(
        target={
            "ref": "doc/r1/fig",
            "children": [
                {"ref": "doc/r1/fig.m0", "attributes": {"reading_index": 0}},
                {"ref": "doc/r1/fig.m1", "attributes": {"reading_index": 1}, "status": "removed"},
            ],
            "structures": [
                {
                    "node_ref": "doc/r1/fig.m0",
                    "e_smiles_raw": "*CC<sep><a>0:R</a><a>1:Y</a><r>0:X</r>",
                    "validation": {"valid": True},
                },
                {"node_ref": "doc/r1/other.m0", "e_smiles_raw": "CCO", "validation": {"valid": True}},
                {"node_ref": "doc/r1/fig.m1", "e_smiles_raw": "CCN", "validation": {"valid": True}},
            ],
        }
    )
    inventory = current_structure_inventory(material)
    assert inventory["structures"][0]["attachment_labels"] == ["R", "Y"]
    assert inventory["structures"][0]["variable_annotations"] == [
        {"kind": "a", "label": "R"},
        {"kind": "a", "label": "Y"},
        {"kind": "r", "label": "X"},
    ]
    from drugpaper.agents.figure.context import structure_placeholder_inventory

    placeholders = structure_placeholder_inventory(material)
    assert placeholders["structures"] == [{"mol_id": 0, "recognized": True}]
    assert "attachment_labels" not in placeholders["structures"][0]
    assert "*CC" not in str(inventory) and "CCO" not in str(inventory)


def test_updating_a_known_gap_can_omit_its_immutable_identity():
    log, args = log_and_gap()
    result = update_gap(
        log,
        {
            "gap_id": "members",
            "status": "unresolved",
            "evidence_ids": ["e1"],
            "note": "Source inspected but assignment remains ambiguous",
        },
        {"members": []},
        False,
        {},
    )
    assert result["gap"]["question"] == args["question"]
    assert result["gap"]["field_paths"] == args["field_paths"]
    with pytest.raises(ValueError, match="New gaps require"):
        update_gap(log, {"gap_id": "new"}, {}, False, {})


def test_old_large_tool_bodies_are_archived_without_dropping_protocol_pairs():
    from drugpaper.gateway.base import Message

    messages = [Message(role="system", content="system"), Message(role="user", content="target")]
    import json

    for i in range(8):
        messages.extend(
            [
                Message(
                    role="assistant", tool_calls=[ToolCall(id=str(i), name="get_structure", arguments={})]
                ),
                Message(
                    role="tool",
                    tool_call_id=str(i),
                    name="get_structure",
                    content=json.dumps(
                        {"ok": True, "extraction_version": i, "extraction": {"text": "x" * 7000}}
                    ),
                ),
            ]
        )
    bounded, usage = compact_context(messages, 2, 100000)
    assert usage["archived_tool_results"] == 2
    assert json.loads(bounded[3].text())["archived_tool_result"]
    assert bounded[3].tool_call_id == "0"
    assert bounded[2].tool_calls == messages[2].tool_calls
    assert not json.loads(messages[3].text()).get("archived_tool_result")
