"""Exercise the public SkillTool boundary, real persistence and nested tool calls."""

from __future__ import annotations

import json

import pytest
from jsonschema import Draft202012Validator

from drugpaper.agent.config import AgentConfig
from drugpaper.agent.runtime import AgentRuntime
from drugpaper.capabilities.meta_tools import make_meta_tools
from drugpaper.capabilities.toolset import DisclosureToolset
from drugpaper.gateway.base import ImagePart
from drugpaper.persistence.models import ArtifactRow
from drugpaper.tools.protocol import ToolContext
from tests.fakes.fake_llm import FakeLLM, ListTracer, call, submit, text, tools

REF = "doc_chem/r1/p000.g000"
VISUAL = (
    "scheme_extract",
    "table_extract",
    "structures_extract",
    "molecular_docking_extract",
    "protac_architecture_extract",
    "metabolic_pathway_extract",
    "pharmacophore_model_extract",
    "chart_digitize",
    "common_extract",
)


@pytest.fixture
async def autonomy_env(figure_env):
    original = figure_env[2].models
    try:
        yield figure_env
    finally:
        figure_env[2].models = original


def visual_response(name):
    if name == "table_extract":
        return {
            "headers": ["Compound"],
            "rows": [["26"]],
            "footnote_markers": {},
            "cell_details": [],
            "molecules": [{"mol_id": 0, "reference": "26", "evidence_text": "printed label"}],
            "compound_instances": [
                {"reference": "26", "mol_id": 0, "row_index": 0, "evidence_text": "printed label"}
            ],
        }
    result = {
        "status": "ok",
        "molecules": [{"mol_id": 0, "reference": "26", "evidence_text": "printed label"}],
    }
    if name == "chart_digitize":
        result.update(
            chart_type="line",
            x_axis={},
            y_axis={},
            series=[],
            plot_area={"x0": 0.1, "y0": 0.1, "x1": 0.9, "y1": 0.9},
            status="unclear",
        )
    else:
        result["compound_instances"] = [{"reference": "26", "mol_id": 0, "evidence_text": "printed label"}]
    return result


async def invoke(bundle, name, inputs=None, **kw):
    return await bundle.tools.invoke(
        ToolContext(run_id="autonomy", trace_id="autonomy"),
        call(name, {"target_ref": REF, "inputs": inputs or {}, **kw}),
    )


async def prerequisites(bundle):
    for name in ("molecule_detect", "molecule_recognize", "som_render"):
        result = await invoke(bundle, name)
        assert result.ok, result.error


@pytest.mark.parametrize("name", VISUAL)
async def test_public_visual_skill_gets_numbered_and_original_then_hands_off_artifact(autonomy_env, name):
    _, backend, bundle, _ = autonomy_env
    await prerequisites(bundle)
    assert backend.calls == 1  # real Skill -> whitelist Tool -> detector service
    model = FakeLLM([text(json.dumps(visual_response(name)))])
    bundle.models = model
    result = await invoke(bundle, name)
    assert result.ok, result.error
    images = [
        part
        for msg in model.requests[0].messages
        if isinstance(msg.content, list)
        for part in msg.content
        if isinstance(part, ImagePart)
    ]
    assert len(images) == 2 and images[0].data_b64 != images[1].data_b64
    material = "\n".join(m.text() for m in model.requests[0].messages)
    assert "CCO" not in material and "e_smiles_raw" not in material
    aid = result.data["artifacts"][0]["artifact_id"]
    filled = await invoke(
        bundle,
        "mol_backfill",
        {"contract": "universal"},
        artifact_inputs={"extraction_payload": {"artifact_id": aid}},
    )
    assert filled.ok, filled.error
    async with bundle.runtime.db.session() as session:
        saved = await session.get(ArtifactRow, filled.data["artifacts"][0]["artifact_id"])
    payload = saved.payload
    assert not payload["errors"]
    assert payload["payload"]["molecules"][0]["molecular_structure"]["canonical_smiles"] == "CCO"
    if name == "chart_digitize":
        assert result.data["needs_review"] is True


async def test_missing_numbered_material_is_recoverable_without_vlm_call(autonomy_env):
    _, _, bundle, _ = autonomy_env
    model = FakeLLM([])
    bundle.models = model
    missing = await invoke(bundle, "structures_extract")
    assert not missing.ok and "som_render" in missing.error and not model.requests
    await prerequisites(bundle)
    bundle.models = FakeLLM([text(json.dumps(visual_response("structures_extract")))])
    assert (await invoke(bundle, "structures_extract")).ok


async def test_stale_numbered_image_is_rejected(autonomy_env):
    sv, backend, bundle, _ = autonomy_env
    await prerequisites(bundle)
    sv._detector._cache.clear()
    backend.predict = lambda *a, **k: []
    assert (await invoke(bundle, "molecule_detect", {"preset": "high_recall"})).ok
    model = FakeLLM([])
    bundle.models = model
    result = await invoke(bundle, "structures_extract")
    assert not result.ok and "重新调用som_render" in result.error and not model.requests


async def test_large_output_retains_ids_and_full_server_side_handoff(autonomy_env):
    _, _, bundle, _ = autonomy_env
    await prerequisites(bundle)
    response = visual_response("structures_extract")
    response["description"] = "原始证据" * 3000
    bundle.models = FakeLLM([text(json.dumps(response))])
    result = await invoke(bundle, "structures_extract")
    assert result.ok and not result.truncated and result.data["payload_omitted"]
    artifact = result.data["artifacts"][0]
    assert "payload" not in artifact and artifact["artifact_id"]
    filled = await invoke(
        bundle,
        "mol_backfill",
        {"contract": "universal"},
        artifact_inputs={"extraction_payload": {"artifact_id": artifact["artifact_id"]}},
    )
    assert filled.ok and filled.data["payload_omitted"] and not filled.truncated
    async with bundle.runtime.db.session() as session:
        saved = await session.get(ArtifactRow, filled.data["artifacts"][0]["artifact_id"])
    assert saved.payload["original"]["description"] == response["description"]
    assert not saved.payload["errors"]


@pytest.mark.parametrize("mode", ["other_user", "other_target", "inactive", "bad_path", "duplicate"])
async def test_artifact_handoff_enforces_ownership_target_and_paths(autonomy_env, mode):
    _, _, bundle, _ = autonomy_env
    await prerequisites(bundle)
    bundle.models = FakeLLM([text(json.dumps(visual_response("structures_extract")))])
    result = await invoke(bundle, "structures_extract")
    aid = result.data["artifacts"][0]["artifact_id"]
    if mode in {"other_user", "other_target", "inactive"}:
        async with bundle.runtime.db.session() as session:
            saved = await session.get(ArtifactRow, aid)
            if mode == "other_user":
                saved.user_id = "other"
            elif mode == "other_target":
                saved.target_ref = "doc_chem/r1/p000.b000"
            else:
                saved.is_active = False
    inputs = {"contract": "universal"}
    if mode == "duplicate":
        inputs["extraction_payload"] = {}
    failed = await invoke(
        bundle,
        "mol_backfill",
        inputs,
        artifact_inputs={
            "extraction_payload": {"artifact_id": aid, "path": ["absent"] if mode == "bad_path" else []}
        },
    )
    assert not failed.ok and failed.error


async def test_agent_discovers_loads_and_runs_skill_with_nested_tool(autonomy_env):
    _, backend, bundle, _ = autonomy_env
    toolset = DisclosureToolset(bundle.tools, [], bundle.index)
    for t in make_meta_tools(bundle.index, toolset):
        bundle.tools.replace(t)
    model = FakeLLM(
        [
            tools(call("find_capabilities", {"query": "molecule_detect"})),
            tools(call("describe_capability", {"name": "molecule_detect"})),
            tools(
                call(
                    "read_skill_resource",
                    {"name": "molecule_detect", "section": "molecular-workflow.md#本类步骤"},
                )
            ),
            tools(call("load_capability", {"name": "molecule_detect"})),
            tools(call("molecule_detect", {"target_ref": REF, "inputs": {"preset": "default"}})),
            submit("已完成检测，后续需要识别、编号和回填。"),
        ]
    )
    result = await AgentRuntime(
        AgentConfig(name="autonomous", system_prompt="发现并使用相关技能", toolset=toolset),
        model,
        bundle.tools,
        ListTracer(),
    ).run("检测当前图像", ToolContext(run_id="react", trace_id="react"))
    assert result.stop_reason == "answered" and backend.calls == 1
    assert "molecule_detect" not in model.tool_names(0) and "molecule_detect" in model.tool_names(4)
    params = next(t.parameters for t in model.requests[4].tools if t.name == "molecule_detect")
    assert params["properties"]["inputs"]["properties"]["preset"]["enum"] == ["default", "high_recall"]
    Draft202012Validator.check_schema(params)


@pytest.mark.parametrize("name", ["molecule_recognize", "som_render"])
async def test_dependent_skill_cannot_certify_work_before_detection(autonomy_env, name):
    _, backend, bundle, _ = autonomy_env
    missing = await invoke(bundle, name)
    assert not missing.ok and "先单独调用molecule_detect" in missing.error
    assert backend.calls == 0


async def test_sar_pending_payload_is_pending_artifact_and_tool_result(autonomy_env):
    _, _, bundle, _ = autonomy_env
    result = await invoke(
        bundle, "sar_record", {"grid": {"rows": [], "header_rows": []}, "require_explicit_instances": True}
    )
    assert result.ok and result.data["needs_review"]
    assert result.data["artifacts"][0]["status"] == "needs_review"


async def test_self_loading_skills_do_not_advertise_unsupported_artifact_bindings(autonomy_env):
    _, _, bundle, _ = autonomy_env
    for name in ("som_render", "molecule_recognize"):
        schema = bundle.tools.get(name).spec.to_schema().parameters
        assert "artifact_inputs" not in schema["properties"]
        assert "服务端" in (await bundle.registry.resolve(name)).l1
        assert list(
            Draft202012Validator(schema).iter_errors({"target_ref": REF, "inputs": {"detections": {}}})
        )
    schema = bundle.tools.get("mol_backfill").spec.to_schema().parameters
    Draft202012Validator(schema).validate(
        {
            "target_ref": REF,
            "inputs": {"contract": "universal"},
            "artifact_inputs": {"extraction_payload": {"artifact_id": "upstream"}},
        }
    )
    assert list(
        Draft202012Validator(schema).iter_errors(
            {"target_ref": REF, "artifact_inputs": {"detections": {"artifact_id": "wrong"}}}
        )
    )


async def test_parameter_errors_are_bounded_and_do_not_echo_entire_source(autonomy_env):
    _, _, bundle, _ = autonomy_env
    await prerequisites(bundle)
    result = await invoke(bundle, "som_render", {"unexpected": "sensitive_source" * 10000})
    assert not result.ok and len(result.error) < 500 and "sensitive_source" not in result.error


async def test_agent_completion_gate_retries_premature_answer(autonomy_env):
    from drugpaper.validators.workflow import RequiredCallsValidator

    _, backend, bundle, _ = autonomy_env
    toolset = DisclosureToolset(bundle.tools, [], bundle.index)
    for tool in make_meta_tools(bundle.index, toolset):
        bundle.tools.replace(tool)
    model = FakeLLM(
        [
            submit("检测已完成"),
            tools(call("load_capability", {"name": "molecule_detect"})),
            tools(call("molecule_detect", {"target_ref": REF, "inputs": {}})),
            submit("实际检测完成"),
        ]
    )
    tracer = ListTracer()
    result = await AgentRuntime(
        AgentConfig(
            name="gated",
            system_prompt="实际检测",
            toolset=toolset,
            validators=[RequiredCallsValidator({"molecule_detect"})],
        ),
        model,
        bundle.tools,
        tracer,
    ).run("实际处理", ToolContext(run_id="completion", trace_id="completion"))
    assert result.stop_reason == "answered" and backend.calls == 1
    checks = tracer.of("validation")
    assert checks[0]["retry"] and not checks[0]["ok"] and checks[-1]["ok"]
    assert tracer.of("tool_call")[-1]["artifact_ids"]


async def test_parent_and_local_recognition_handoff_uses_current_node_pointer(autonomy_env):
    from drugpaper.facts.molecular_delivery import deliver

    _, _, bundle, run = autonomy_env
    await prerequisites(bundle)
    ref = REF + ".m000"
    # Different work units legitimately retain history, but only the node's
    # current projected candidate is an input for new molecular delivery.
    local = await run("molecule_recognize", ref=ref, force=True)
    assert local.status == "completed"
    context = await bundle.context_for(ToolContext(run_id="read", trace_id="read"), REF)
    structures = [s for s in context.materials.target["structures"] if s["node_ref"] == ref]
    assert len(structures) == 1
    child = context.materials.target["children"][0]
    assert structures[0]["structure_id"] == child["attributes"]["structure_id"]
    structures[0]["validation"]["valid"] = False
    bad = deliver({"molecules": [{"mol_id": 0}]}, [{"mol_id": 0, "node_ref": ref}], structures)
    assert any(e["code"] == "invalid_structure" for e in bad["errors"])


async def test_recognition_artifact_is_not_visual_extraction(autonomy_env):
    _, _, bundle, _ = autonomy_env
    await prerequisites(bundle)
    recognition = await invoke(bundle, "molecule_recognize")
    result = await invoke(
        bundle,
        "mol_backfill",
        {"contract": "universal"},
        artifact_inputs={
            "extraction_payload": {"artifact_id": recognition.data["artifacts"][0]["artifact_id"]}
        },
    )
    assert not result.ok and "不能代替视觉抽取" in result.error


@pytest.mark.parametrize("name", ["structures_extract", "table_extract"])
async def test_visual_skill_rejects_chemistry_smuggled_through_agent_inputs(autonomy_env, name):
    _, _, bundle, _ = autonomy_env
    await prerequisites(bundle)
    model = FakeLLM([])
    bundle.models = model
    result = await invoke(
        bundle, name, {"upstream": {"molecules": [{"mol_id": 0, "canonical_smiles": "CCO"}]}}
    )
    assert not result.ok and "只能使用mol_id" in result.error and not model.requests


@pytest.mark.parametrize("kind", ["molecule_boxes", "som_image"])
async def test_failed_visual_material_rejected_before_model(autonomy_env, kind):
    sv, _, bundle, _ = autonomy_env
    await prerequisites(bundle)
    from sqlalchemy import select

    async with sv.db.session() as session:
        row = (
            await session.execute(
                select(ArtifactRow).where(
                    ArtifactRow.target_ref == REF,
                    ArtifactRow.artifact_type == kind,
                    ArtifactRow.is_active.is_(True),
                )
            )
        ).scalar_one()
        row.status = "failed"
    model = FakeLLM([])
    bundle.models = model
    result = await invoke(bundle, "structures_extract")
    assert not result.ok and "执行失败" in result.error and not model.requests
