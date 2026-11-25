"""Exercise actual tool dispatch and document scoping without remote model calls."""

from types import MethodType

import pytest

from drugpaper.agents.figure.accounting import CallAccounting
from drugpaper.agents.figure.exploration import explore
from drugpaper.agents.figure.graph import FigureAgent
from drugpaper.gateway.base import ChatResponse, ToolCall


class Scripted:
    def __init__(self, calls):
        self.calls = iter(calls)
        self.requests = []

    async def aclose(self):
        pass

    async def chat(self, req, *, task):
        assert task == "figure_explore"
        assert req.tools
        self.requests.append(req)
        name, arguments = next(self.calls)
        return ChatResponse(
            content="按证据决定下一步",
            tool_calls=[ToolCall(id=f"call{len(self.requests)}", name=name, arguments=arguments)],
        )


async def harness(figure_env, calls):
    sv, _, bundle, _ = figure_env
    sv.settings.figure_exploration_enabled = True
    agent = FigureAgent(sv)
    model = Scripted(calls)
    bundle.models = model
    state = {
        "document_id": "doc_chem",
        "node_ref": "doc_chem/r1/p000.g000",
        "user_id": "default",
        "run_id": "run_explore",
        "trace_id": "tr_explore",
        "material": {},
        "chain": [],
        "classification": {},
        "accounting": CallAccounting().model_dump(),
        "trace": [],
    }

    async def extract(self, state):
        state["extraction"] = {"status": "completed", "evidence_count": len(state["exploration"]["evidence"])}
        return state

    async def post(self, state):
        state["backfilled_extraction"] = state["extraction"].copy()
        return state

    async def check(self, state):
        state["checks"] = []
        return state

    agent._structure_once = MethodType(extract, agent)
    agent._post = MethodType(post, agent)
    agent._check_l1 = MethodType(check, agent)
    return agent, state, model


FINISH = ("finish", {"summary": "已根据证据提取并校验", "unresolved": []})


@pytest.mark.parametrize("name", ["inspect_assembly", "try_assembly", "apply_assembly", "recognize_molecule"])
async def test_structure_phase_hides_and_rejects_molecular_tools(figure_env, name):
    from drugpaper.agents.figure.exploration import MOLECULAR_RECOVERY_TOOLS

    agent, state, model = await harness(
        figure_env, [(name, {}), ("extract_figure", {}), ("validate_result", {}), FINISH]
    )
    state["material"] = {
        "formula": "PRIVATE_FORMULA",
        "nested": {"canonical_smiles": "PRIVATE_STRUCTURE"},
        "text_tokens": ["Document says R=Me"],
    }
    await explore(agent, state)
    result = next(
        s["result"]
        for s in state["exploration"]["steps"]
        if s.get("kind") == "tool" and s.get("name") == name
    )
    assert result["repair"]["code"] == "phase_constraint"
    assert not MOLECULAR_RECOVERY_TOOLS & {t.name for t in model.requests[0].tools}
    assert "PRIVATE_FORMULA" not in model.requests[0].model_dump_json()
    assert "PRIVATE_STRUCTURE" not in model.requests[0].model_dump_json()
    assert "Document says R=Me" in model.requests[0].model_dump_json()


async def test_successful_backfill_does_not_open_molecular_tools(figure_env):
    from drugpaper.agents.figure.exploration import MOLECULAR_RECOVERY_TOOLS

    agent, state, model = await harness(
        figure_env, [("extract_figure", {}), ("validate_result", {}), ("inspect_assembly", {}), FINISH]
    )
    await explore(agent, state)
    assert not MOLECULAR_RECOVERY_TOOLS & {t.name for t in model.requests[2].tools}
    rejected = next(
        s
        for s in state["exploration"]["steps"]
        if s.get("name") == "inspect_assembly" and s.get("kind") == "tool"
    )
    assert rejected["result"]["repair"]["code"] == "phase_constraint"


async def test_failed_empty_reextraction_preserves_previous_routes(figure_env):
    agent, state, _ = await harness(
        figure_env,
        [
            ("get_node", {"node_id": "p000.g000"}),
            ("extract_figure", {}),
            ("extract_figure", {}),
            ("validate_result", {}),
            FINISH,
        ],
    )
    calls = 0

    async def extract(self, state):
        nonlocal calls
        calls += 1
        state["extraction"] = {"status": "unclear", "pathways": [{"steps": []}] if calls == 1 else []}
        return state

    agent._structure_once = MethodType(extract, agent)
    await explore(agent, state)
    assert state["extraction"]["pathways"] == [{"steps": []}]
    assert state["exploration"]["extractions"] == 1
    failures = [
        s
        for s in state["exploration"]["steps"]
        if s.get("name") == "extract_figure" and not s.get("result", {}).get("ok", True)
    ]
    assert "extraction_content_lost" in failures[0]["result"]["error"]


async def test_native_tools_feed_evidence_then_extract_and_validate(figure_env):
    agent, state, model = await harness(
        figure_env,
        [("get_node", {"node_id": "p000.g000"}), ("extract_figure", {}), ("validate_result", {}), FINISH],
    )
    state["material"]["structures"] = [{"e_smiles_raw": "recognition-private-chemical-string"}]
    await explore(agent, state)
    assert state["exploration"]["status"] == "completed"
    assert state["extraction"]["evidence_count"] == 1
    assert state["exploration"]["tool_calls"] == 4
    assert "document_map" in model.requests[0].messages[1].text()
    assert "recognition-private-chemical-string" not in model.requests[0].messages[1].text()
    assert "structure_inventory" in model.requests[0].messages[1].text()
    assert state["accounting"]["exploration_calls"] == 4
    assert any(m.role == "tool" for m in model.requests[-1].messages)


async def test_agent_cannot_read_other_document_or_call_shell(figure_env):
    agent, state, _ = await harness(
        figure_env,
        [
            ("get_node", {"node_id": "doc_other/r1/p000.g000"}),
            ("inspect_visual", {"node_id": "doc_other/r1/p000.g000"}),
            ("shell", {"command": "cat .env"}),
            ("extract_figure", {}),
            ("validate_result", {}),
            FINISH,
        ],
    )
    await explore(agent, state)
    results = [x["result"] for x in state["exploration"]["steps"] if x["kind"] == "tool"]
    assert all(not r["ok"] for r in results[:3])
    assert state["exploration"]["evidence"] == []


async def test_finish_rejected_until_latest_evidence_extracted_and_validated(figure_env):
    agent, state, _ = await harness(
        figure_env,
        [
            FINISH,
            ("extract_figure", {}),
            ("validate_result", {}),
            ("get_node", {"node_id": "p000.g000"}),
            FINISH,
            ("extract_figure", {}),
            ("validate_result", {}),
            FINISH,
        ],
    )
    await explore(agent, state)
    rejected = [
        e
        for e in state["exploration"]["steps"]
        if e["kind"] == "tool" and e["name"] == "finish" and not e["result"]["ok"]
    ]
    assert len(rejected) == 2
    assert state["exploration"]["extractions"] == 2
    assert state["exploration"]["status"] == "completed"


async def test_tool_budget_preserves_partial_result_as_needs_review(figure_env):
    agent, state, _ = await harness(
        figure_env, [("extract_figure", {}), ("get_outline", {}), ("validate_result", {})]
    )
    agent.services.settings.figure_exploration_max_tool_calls = 2
    await explore(agent, state)
    assert state["extraction"]
    assert state["exploration"]["status"] == "needs_review"
    assert state["exploration"]["stop_reason"] == "tool_budget"


async def test_repeated_failures_stop_without_fabricating_output(figure_env):
    agent, state, _ = await harness(figure_env, [("shell", {})] * 6)
    with pytest.raises(RuntimeError, match="no_progress"):
        await explore(agent, state)
    assert "extraction" not in state
    assert state["exploration"]["tool_calls"] == 4


async def test_inspected_visual_is_persisted_as_reusable_evidence(figure_env):
    agent, state, _ = await harness(
        figure_env,
        [
            ("inspect_visual", {"node_id": "p000.g000"}),
            ("extract_figure", {}),
            ("validate_result", {}),
            FINISH,
        ],
    )
    await explore(agent, state)
    evidence = state["exploration"]["evidence"][0]
    assert evidence["tool"] == "inspect_visual"
    assert evidence["result"]["node_ref"] == state["node_ref"]
    image = evidence["result"]["image_refs"][0]
    assert await agent.services.store_for("default").get(image["blob_ref"])
    starts = {
        (e["turn"], e["call_id"]): e for e in state["exploration"]["steps"] if e["kind"] == "tool_start"
    }
    for event in state["exploration"]["steps"]:
        if event["kind"] == "tool":
            assert (event["turn"], event["call_id"]) in starts
            assert event["duration_ms"] >= 0
        if event["kind"] == "context":
            assert event["turn"] >= 1
            assert len(event["image_refs"]) == event["active_images"]
            for ref in event["image_refs"]:
                assert await agent.services.store_for("default").get(ref["blob_ref"])


async def test_owner_check_precedes_model_or_document_tools(figure_env):
    agent, state, model = await harness(figure_env, [])
    state["user_id"] = "other_user"
    with pytest.raises((PermissionError, ValueError)):
        await explore(agent, state)
    assert model.requests == []


async def test_cancellation_propagates_and_does_not_continue_extraction(figure_env):
    import asyncio

    agent, state, model = await harness(figure_env, [])

    async def cancelled(*args, **kwargs):
        raise asyncio.CancelledError()

    model.chat = cancelled
    with pytest.raises(asyncio.CancelledError):
        await explore(agent, state)
    assert "extraction" not in state


async def test_unresolved_cannot_finish_without_investigating(figure_env):
    pending = ("finish", {"summary": "缺少定义", "unresolved": ["变量定义不明"]})
    agent, state, _ = await harness(
        figure_env,
        [
            ("extract_figure", {}),
            ("validate_result", {}),
            pending,
            ("get_page_text", {"page": 0}),
            ("extract_figure", {}),
            ("validate_result", {}),
            pending,
        ],
    )
    await explore(agent, state)
    finishes = [e for e in state["exploration"]["steps"] if e.get("name") == "finish" and e["kind"] == "tool"]
    assert not finishes[0]["result"]["ok"]
    assert finishes[1]["result"]["ok"]
    assert state["exploration"]["status"] == "needs_review"


@pytest.mark.parametrize("visual_tool", ["inspect_visual", "inspect_page"])
async def test_extraction_skill_receives_retrieved_image_not_only_reference(figure_env, visual_tool):
    import base64

    from tests.unit.figure.test_agent import CLASSIFICATION, FigureFake

    sv, _, bundle, _ = figure_env
    bundle.models = model = FigureFake()
    agent = FigureAgent(sv)
    state = {
        "document_id": "doc_chem",
        "node_ref": "doc_chem/r1/p000.g000",
        "user_id": "default",
        "run_id": "run_image_evidence",
        "trace_id": "tr_image",
        "classification": CLASSIFICATION,
        "chain": ["table_extract"],
        "material": {"text_tokens": [], "caption": "", "source_text": []},
        "accounting": CallAccounting().model_dump(),
        "artifact_ids": [],
        "trace": [],
    }
    _, ctx = await agent._ctx(state)
    image = ctx.materials.images[0]
    ref = await sv.store_for("default").put(base64.b64decode(image.data_b64), image.mime)
    state["exploration"] = {
        "status": "running",
        "evidence": [
            {
                "tool": visual_tool,
                "result": {
                    "node_ref": state["node_ref"],
                    "image_refs": [{"blob_ref": ref, "mime": image.mime}],
                },
            }
        ],
    }
    await agent._structure_once(state)
    request = model.calls[-1][1]
    assert "Related evidence image (not the extraction target" in "".join(m.text() for m in request.messages)
    from drugpaper.gateway.base import ImagePart

    assert (
        sum(
            isinstance(p, ImagePart)
            for m in request.messages
            if isinstance(m.content, list)
            for p in m.content
        )
        >= 2
    )


async def test_page_and_zoom_images_reach_agent_and_are_distinct(figure_env):
    from drugpaper.gateway.base import ImagePart

    agent, state, model = await harness(
        figure_env,
        [
            ("inspect_page", {"page": 0}),
            (
                "inspect_crop",
                {
                    "source_evidence_id": "e1",
                    "bbox": [0.1, 0.2, 0.6, 0.7],
                    "reason": "整页中的脚注太小，放大查看",
                },
            ),
            ("extract_figure", {}),
            ("validate_result", {}),
            FINISH,
        ],
    )
    await explore(agent, state)
    evidence = state["exploration"]["evidence"]
    assert len(evidence) == 2
    assert evidence[0]["result"]["image_refs"][0]["bbox"] != evidence[1]["result"]["image_refs"][0]["bbox"]
    assert state["exploration"]["visual_reads"] == 2
    assert any(
        isinstance(p, ImagePart)
        for m in model.requests[-1].messages
        if isinstance(m.content, list)
        for p in m.content
    )
    assert evidence[1]["result"]["image_refs"][0]["max_side"] == 2000


@pytest.mark.parametrize(
    "name,args",
    [
        ("inspect_page", {"page": 999}),
        ("inspect_page", {"page": 0, "bbox": [0, 0, 2, 1]}),
        ("inspect_visual", {"node_id": "doc_other/r1/p000.g000"}),
        ("inspect_visual", {"node_id": "p000.g000", "region_index": 99}),
    ],
)
async def test_visual_invalid_scope_and_geometry_return_tool_errors(figure_env, name, args):
    agent, state, _ = await harness(
        figure_env, [(name, args), ("extract_figure", {}), ("validate_result", {}), FINISH]
    )
    await explore(agent, state)
    result = next(
        e["result"] for e in state["exploration"]["steps"] if e["kind"] == "tool" and e["name"] == name
    )
    assert not result["ok"]
    assert not state["exploration"]["evidence"]


async def test_visual_budget_does_not_discard_previous_images(figure_env):
    agent, state, _ = await harness(
        figure_env,
        [
            ("inspect_page", {"page": 0}),
            ("inspect_visual", {"node_id": "p000.g000"}),
            ("extract_figure", {}),
            ("validate_result", {}),
            FINISH,
        ],
    )
    agent.services.settings.figure_exploration_max_visual_reads = 1
    await explore(agent, state)
    assert len(state["exploration"]["evidence"]) == 1
    assert any("budget" in e.get("result", {}).get("error", "") for e in state["exploration"]["steps"])


async def test_markush_uncertainty_requires_related_table_visual_check(figure_env):
    from drugpaper.persistence.models import NodeRow

    pending = ("finish", {"summary": "定义仍不清楚", "unresolved": ["通式成员待确认"]})
    agent, state, _ = await harness(
        figure_env,
        [
            ("get_page_text", {"page": 0}),
            ("extract_figure", {}),
            ("validate_result", {}),
            pending,
            ("inspect_page", {"page": 0}),
            ("extract_figure", {}),
            ("validate_result", {}),
            pending,
        ],
    )
    async with agent.services.db.session() as session:
        node = await session.get(NodeRow, ("doc_chem", 1, "p000.b000"))
        node.node_type = "table"
        node.page = 0
        node.geometry = {"regions": [{"page": 0, "bbox": {"x0": 0, "y0": 0, "x1": 1, "y1": 1}}]}

    async def post(self, state):
        state["material"]["backfill_errors"] = [{"code": "markush_candidate_unresolved"}]
        return state

    agent._post = MethodType(post, agent)
    await explore(agent, state)
    finishes = [
        e["result"] for e in state["exploration"]["steps"] if e["kind"] == "tool" and e["name"] == "finish"
    ]
    assert "visual verification" in finishes[0]["error"]
    assert finishes[1]["ok"]
    assert state["exploration"]["status"] == "needs_review"


async def test_existing_node_precedes_its_whole_page(figure_env):
    from drugpaper.persistence.models import NodeRow

    agent, state, _ = await harness(
        figure_env,
        [
            ("inspect_page", {"page": 1}),
            ("inspect_visual", {"node_id": "p000.b000"}),
            ("inspect_page", {"page": 1, "reason": "单表无法确认周围图注，查看所在整页"}),
            ("extract_figure", {}),
            ("validate_result", {}),
            FINISH,
        ],
    )
    async with agent.services.db.session() as session:
        node = await session.get(NodeRow, ("doc_chem", 1, "p000.b000"))
        node.node_type = "table"
        node.page = 1
        node.geometry = {"regions": [{"page": 1, "bbox": {"x0": 0.1, "y0": 0.1, "x1": 0.9, "y1": 0.9}}]}
    await explore(agent, state)
    calls = [e for e in state["exploration"]["steps"] if e["kind"] == "tool"]
    assert "Prefer existing" in calls[0]["result"]["error"]
    assert calls[1]["result"]["ok"] and calls[2]["result"]["ok"]
    assert state["exploration"]["visual_reads"] == 2


async def test_crop_is_hidden_until_full_page_and_rejects_blind_bbox(figure_env):
    agent, state, model = await harness(
        figure_env,
        [
            ("inspect_visual", {"node_id": "p000.g000", "bbox": [0.1, 0.1, 0.9, 0.9]}),
            ("inspect_crop", {"source_evidence_id": "nonexistent", "bbox": [0, 0, 1, 1], "reason": "放大"}),
            ("inspect_page", {"page": 0}),
            (
                "inspect_crop",
                {"source_evidence_id": "e1", "bbox": [0, 0, 0.5, 0.5], "reason": "脚注文字太小，放大核实"},
            ),
            ("extract_figure", {}),
            ("validate_result", {}),
            FINISH,
        ],
    )
    await explore(agent, state)
    assert all(t.name != "inspect_crop" for t in model.requests[0].tools)
    assert any(t.name == "inspect_crop" for t in model.requests[3].tools)
    for req in model.requests:
        for tool in req.tools:
            if tool.name in {"inspect_visual", "inspect_page"}:
                assert "bbox" not in tool.parameters["properties"]
    results = [e["result"] for e in state["exploration"]["steps"] if e["kind"] == "tool"]
    assert not results[0]["ok"] and not results[1]["ok"]
    assert results[3]["ok"] and results[3]["source_evidence_id"] == "e1"
    assert state["exploration"]["visual_reads"] == 2


async def test_transient_extract_failure_does_not_spend_success_budget(figure_env):
    agent, state, _ = await harness(
        figure_env, [("extract_figure", {}), ("extract_figure", {}), ("validate_result", {}), FINISH]
    )
    original = agent._structure_once
    attempts = 0

    async def extract(self, state):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("upstream unavailable")
        return await original(state)

    agent._structure_once = MethodType(extract, agent)
    agent.services.settings.figure_exploration_max_extractions = 1
    await explore(agent, state)
    assert state["exploration"]["status"] == "completed"
    assert state["exploration"]["extractions"] == 1
    assert state["exploration"]["extraction_attempts"] == 2


async def test_visual_requirement_prevents_wasted_reextraction(figure_env):
    from drugpaper.persistence.models import NodeRow

    agent, state, _ = await harness(
        figure_env,
        [
            ("extract_figure", {}),
            ("validate_result", {}),
            ("get_page_text", {"page": 0}),
            ("extract_figure", {}),
            ("inspect_page", {"page": 0}),
            ("extract_figure", {}),
            ("validate_result", {}),
            FINISH,
        ],
    )
    async with agent.services.db.session() as session:
        row = await session.get(NodeRow, ("doc_chem", 1, "p000.b000"))
        row.node_type = "table"
        row.page = 0
        row.geometry = {"regions": [{"page": 0, "bbox": {"x0": 0, "y0": 0, "x1": 1, "y1": 1}}]}

    async def post(self, state):
        state["material"]["backfill_errors"] = [{"code": "markush_candidate_unresolved"}]
        return state

    agent._post = MethodType(post, agent)
    await explore(agent, state)
    results = [
        e["result"]
        for e in state["exploration"]["steps"]
        if e["kind"] == "tool" and e["name"] == "extract_figure"
    ]
    assert "visual verification" in results[1]["error"]
    assert state["exploration"]["extractions"] == 2
    assert state["exploration"]["unapplied_evidence"] == []


async def test_coarse_preview_then_high_resolution_evidence_and_gap_resolution(figure_env):
    gap = {"gap_id": "g1", "question": "如何映射编号", "field_paths": ["/members"]}
    agent, state, model = await harness(
        figure_env,
        [
            ("update_gap", gap),
            ("get_document_map", {"offset": 0, "limit": 2}),
            ("preview_pages", {"pages": [0, 1], "gap_ids": ["g1"], "reason": "定位编号表格"}),
            ("inspect_visual", {"node_id": "p000.g000"}),
            ("extract_figure", {}),
            ("validate_result", {}),
            (
                "update_gap",
                {**gap, "status": "resolved", "evidence_ids": ["e2"], "note": "高清表格中已确认编号"},
            ),
            FINISH,
        ],
    )

    async def extract(self, state):
        state["extraction"] = {"status": "completed", "members": ["1", "2"]}
        return state

    agent._structure_once = MethodType(extract, agent)
    await explore(agent, state)
    log = state["exploration"]
    assert log["status"] == "completed"
    assert log["preview_batches"] == 1
    assert log["visual_reads"] == 2
    assert log["gaps"][0]["status"] == "resolved"
    assert log["applied_evidence_ids"] == ["e2"]
    assert log["unapplied_evidence"] == []
    assert len(log["evidence"][0]["result"]["image_refs"]) == 2
    assert "inspect_crop" not in {t.name for t in model.requests[3].tools}
    assert any(
        m.role == "user" and "precision" in m.text() and "preview" in m.text()
        for m in model.requests[3].messages
    )


@pytest.mark.parametrize("pages", [[999], [0, 0], [True]])
async def test_preview_rejects_invalid_pages_without_evidence_or_budget_use(figure_env, pages):
    agent, state, _ = await harness(
        figure_env,
        [
            ("preview_pages", {"pages": pages}),
            ("extract_figure", {}),
            ("validate_result", {}),
            FINISH,
        ],
    )
    await explore(agent, state)
    log = state["exploration"]
    assert log["preview_batches"] == 0
    assert not log["evidence"]
    assert not next(
        e["result"]["ok"]
        for e in log["steps"]
        if e.get("kind") == "tool" and e.get("name") == "preview_pages"
    )


async def test_preview_is_not_high_resolution_proof_and_cannot_grant_crop_permission(figure_env):
    agent, state, model = await harness(
        figure_env,
        [
            ("preview_pages", {"pages": [0]}),
            ("inspect_crop", {"source_evidence_id": "e1", "bbox": [0, 0, 0.5, 0.5], "reason": "放大"}),
            ("select_context", {"evidence_ids": ["e1"]}),
            ("extract_figure", {}),
            ("validate_result", {}),
            FINISH,
        ],
    )
    await explore(agent, state)
    results = [e for e in state["exploration"]["steps"] if e.get("kind") == "tool"]
    assert not results[1]["result"]["ok"]
    assert not results[2]["result"]["ok"]
    assert "inspect_crop" not in {t.name for t in model.requests[1].tools}
    assert state["exploration"]["applied_evidence_ids"] == []


async def test_preview_budget_and_selection_reload_with_bounded_history(figure_env):
    from drugpaper.gateway.base import ImagePart

    agent, state, model = await harness(
        figure_env,
        [
            ("preview_pages", {"pages": [0, 1]}),
            ("preview_pages", {"pages": [0]}),
            ("inspect_visual", {"node_id": "p000.g000"}),
            ("select_context", {"evidence_ids": ["e2"]}),
            ("extract_figure", {}),
            ("validate_result", {}),
            FINISH,
        ],
    )
    agent.services.settings.figure_exploration_max_preview_batches = 1
    agent.services.settings.figure_exploration_max_context_images = 3
    await explore(agent, state)
    log = state["exploration"]
    assert log["preview_batches"] == 1
    assert log["selected_evidence_ids"] == ["e2"]
    assert log["applied_evidence_ids"] == ["e2"]
    assert any(e["kind"] == "context" and e["released_images"] > 0 for e in log["steps"])
    for request in model.requests:
        assert (
            sum(
                isinstance(p, ImagePart)
                for m in request.messages
                if isinstance(m.content, list)
                for p in m.content
            )
            <= 3
        )


async def test_unclosed_registered_gap_prevents_false_completed_status(figure_env):
    agent, state, _ = await harness(
        figure_env,
        [
            ("update_gap", {"gap_id": "g1", "question": "编号尚未核对", "field_paths": ["/members"]}),
            ("get_page_text", {"page": 0}),
            ("extract_figure", {}),
            ("validate_result", {}),
            FINISH,
        ],
    )
    await explore(agent, state)
    assert state["exploration"]["status"] == "needs_review"
    assert "编号尚未核对" in state["exploration"]["unresolved"]
