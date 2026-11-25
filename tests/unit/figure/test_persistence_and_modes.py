import json

import pytest
from sqlalchemy import select

from drugpaper.agents.figure.graph import FigureAgent
from drugpaper.gateway.base import ChatResponse
from drugpaper.gateway.task_router import TaskRouter
from drugpaper.orchestration.figure_pipeline import FigurePipeline
from drugpaper.persistence.models import AssayRow, MeasurementRow, NodeRow
from drugpaper.tools.protocol import ToolContext
from tests.unit.figure.test_agent import GRID, FigureFake


async def test_measurements_replace_atomically_with_evidence_and_owner(figure_env):
    sv, _, bundle, run = figure_env
    await run("measurement_map", inputs={"grid": GRID})
    await run("measurement_map", inputs={"grid": {**GRID, "subject_compound_label": "27"}}, force=True)
    async with sv.db.session() as session:
        rows = list((await session.execute(select(MeasurementRow))).scalars())
        assays = list((await session.execute(select(AssayRow).where(AssayRow.is_active.is_(True)))).scalars())
    assert len(rows) == 2 and sum(r.is_active for r in rows) == 1
    active = next(r for r in rows if r.is_active)
    assert active.payload["subject_label"] == "27"
    assert active.payload["evidence"][0]["node_id"] == "doc_chem/r1/p000.g000"
    assert assays and active.payload["produced_by_artifact_id"] == active.artifact_id
    with pytest.raises(PermissionError):
        await FigurePipeline(sv).run("doc_chem", user_id="other")


async def test_detection_children_have_refs_and_panel_geometry_is_owned(figure_env):
    sv, _, bundle, run = figure_env
    await run("molecule_detect")
    ctx = await bundle.context_for(
        ToolContext(user_id="default", document_id="doc_chem", run_id="r", trace_id="t"),
        "doc_chem/r1/p000.g000",
    )
    children = ctx.materials.target["children"]
    assert children and all(n["ref"].startswith("doc_chem/r1/") for n in children)
    await run("panel_split", inputs={"panels": [{"x0": 0.1, "y0": 0.2, "x1": 0.5, "y1": 0.7}]})
    async with sv.db.session() as session:
        panel = await session.get(NodeRow, ("doc_chem", 1, "p000.g000.panel000"))
        assert panel.geometry["regions"][0]["bbox"]["x0"] == pytest.approx(0.18)
        panel.attributes = {"locked_fields": ["geometry"]}
    result = await bundle.runtime.run(
        "panel_split",
        ctx.target_ref,
        {"panels": [{"x0": 0.2, "y0": 0.2, "x1": 0.6, "y1": 0.7}]},
        ctx,
        force=True,
    )
    assert result.status == "failed" and "locked" in result.error


async def test_query_is_readonly_and_review_requires_previous(figure_env):
    sv, _, bundle, _ = figure_env

    class ModeFake(FigureFake):
        async def chat(self, req, *, task="default"):
            if task == "figure_query":
                self.calls.append((task, req))
                assert not req.tools
                return ChatResponse(
                    content=json.dumps(
                        {"answers": [{"question": "value?", "answer": "50", "evidence_text": "50"}]}
                    )
                )
            return await super().chat(req, task=task)

    fake = ModeFake()
    fake.router = TaskRouter.from_file(sv.settings.models_config_path)
    bundle.models = fake
    with pytest.raises(ValueError, match="prior"):
        await FigureAgent(sv).run("doc_chem/r1/p000.g000", mode="review")
    full = await FigureAgent(sv).run("doc_chem/r1/p000.g000")
    async with sv.db.session() as session:
        before = list((await session.execute(select(MeasurementRow))).scalars())
    query = await FigureAgent(sv).run(full.node_ref, mode="query", questions=["value?"])
    review = await FigureAgent(sv).run(full.node_ref, mode="review")
    assert query.query_answers[0]["answer"] == "50" and query.accounting.total == 1
    assert review.status == "ok" and review.accounting.total == 1
    async with sv.db.session() as session:
        assert len(list((await session.execute(select(MeasurementRow))).scalars())) == len(before)


def test_prompt_view_omits_global_material_and_catalog():
    from drugpaper.skills.prompt_materials import project_target

    target = {
        "ref": "d/r1/p000.g000",
        "spans": [{"text": "secret"}] * 10000,
        "text": "whole document",
        "blackboard": {"labels": list(range(10000)), "references": {"unrelated": ["secret"]}},
        "children": [],
    }
    for name in ["visual_classify", "figure_self_check", "table_extract"]:
        view = project_target(name, target)
        assert "secret" not in json.dumps(view) and "whole document" not in json.dumps(view)
        assert len(json.dumps(view)) < 2000


def test_package_nested_schema_is_exposed_and_validated():
    from pydantic import ValidationError, create_model

    from drugpaper.facts.figure_models import VisualExtraction
    from drugpaper.skills.execution import PackageOutput

    output = create_model("visual_output_test", __base__=PackageOutput)
    output.package_schema = VisualExtraction.model_json_schema()
    assert output.model_json_schema()["$defs"]["MoleculeReference"]
    assert output.model_validate({"status": "ok", "molecules": []}).root["status"] == "ok"
    with pytest.raises(ValidationError, match="package schema"):
        output.model_validate({"status": "success"})


def test_html_superscript_values_preserve_raw_and_parse():
    from drugpaper.facts.value_parser import parse

    result = parse("79 (K<sub>d</sub> = 1.56 × 10<sup>−4</sup> M)")
    assert result.value == 79 and result.secondary[0]["value"] == pytest.approx(1.56e-4)
    assert "<sup>" in result.raw


async def test_panels_accept_normalized_bbox_arrays_and_chart_extra_components(figure_env):
    sv, _, _, run = figure_env
    result = await run("panel_split", inputs={"panels": [{"panel_id": "A", "bbox": [0.1, 0.1, 0.5, 0.5]}]})
    assert result.artifacts[0].payload["panels"][0]["x0"] == 0.1
    chart = {
        "chart_type": "scatter",
        "x_axis": {"title": "time"},
        "y_axis": {"title": "EC50", "unit": "μM"},
        "plot_area": {"x0": 0, "y0": 0, "x1": 1, "y1": 1},
        "series": [{"label": "26", "reference": "26", "points": [[1, 2, 0.1]]}],
    }
    result = await run("chart_measurements", inputs={"dataset": chart})
    assert result.artifacts[0].payload["measurements"][0]["value"] == 2


async def test_sar_scaffold_uses_confirmed_mol_id_not_first_markush(figure_env, monkeypatch):
    from drugpaper.skills.figure import sar_record
    from drugpaper.skills.protocol import ArtifactDraft, SkillResult

    sv, _, bundle, _ = figure_env
    ctx = await bundle.context_for(
        ToolContext(user_id="default", document_id="doc_chem", run_id="r", trace_id="t"),
        "doc_chem/r1/p000.g000",
    )
    ctx.materials.target["children"] = [
        {"ref": "fragment", "attributes": {"reading_index": 0, "molecule_type": "substituent"}},
        {"ref": "scaffold", "attributes": {"reading_index": 1, "molecule_type": "scaffold"}},
    ]
    ctx.materials.target["structures"] = [
        {"node_ref": "fragment", "is_markush": True, "e_smiles_raw": "fragment-raw"},
        {"node_ref": "scaffold", "is_markush": True, "e_smiles_raw": "scaffold-raw"},
    ]
    seen = []

    async def assemble(inputs, context):
        seen.append(inputs["caption"])
        return SkillResult(artifacts=[ArtifactDraft(artifact_type="markush", payload={})])

    monkeypatch.setattr("drugpaper.skills.molecule.markush_assemble", assemble)
    grid = {
        "orientation": "compounds_as_rows",
        "scaffold_mol_id": 1,
        "header_rows": [[{"text": "Compound"}, {"text": "R1"}]],
        "rows": [[{"text": "1"}, {"text": "Cl"}]],
    }
    result = await sar_record({"grid": grid}, ctx)
    assert seen == ["scaffold-raw"] and result.artifacts[0].payload["rows"][0]["label"] == "1"


async def test_sar_retains_full_structures_and_properties_by_row(figure_env):
    from drugpaper.skills.figure import sar_record

    _, _, bundle, _ = figure_env
    ctx = await bundle.context_for(
        ToolContext(user_id="default", document_id="doc_chem", run_id="r", trace_id="t"),
        "doc_chem/r1/p000.g000",
    )
    ctx.materials.target["children"] = [{"ref": "scaffold", "attributes": {"reading_index": 0}}]
    ctx.materials.target["structures"] = [
        {"node_ref": "scaffold", "is_markush": True, "e_smiles_raw": "C1CCCCC1*<sep><a>6:R</a>"}
    ]
    grid = {
        "orientation": "compounds_as_rows",
        "scaffold_mol_id": 0,
        "header_rows": [[{"text": "Compound"}, {"text": "R"}, {"text": "EC50 (nM)"}]],
        "rows": [
            [{"text": "101"}, {"text": "Me"}, {"text": "7 ± 1"}],
            [{"text": "101"}, {"text": "Cl"}, {"text": "12 ± 2"}],
        ],
    }
    result = await sar_record({"grid": grid}, ctx)
    payload = result.artifacts[0].payload
    assert payload["status"] == "completed"
    a, b = payload["rows"]
    assert a["products"] == ["CC1CCCCC1"]
    assert a["structures"][0]["formula"] == "C7H14"
    assert b["structures"][0]["formula"] == "C6H11Cl"
    assert a["properties"][0]["value"] == 7 and a["properties"][0]["error"] == 1
    assert b["properties"][0]["value"] == 12 and b["properties"][0]["error"] == 2
    assert a["row_index"] == 0 and b["row_index"] == 1
    grid["header_rows"][0][1]["text"] = "R position"
    skipped = (await sar_record({"grid": grid}, ctx)).artifacts[0].payload
    assert skipped["status"] == "needs_review"
    assert skipped["rows"][0]["structures"] == []
    assert skipped["rows"][0]["error_code"] == "positional_variable"
    assert skipped["rows"][0]["properties"][0]["value"] == 7


async def test_sar_unresolved_fragment_never_assembles_placeholder(figure_env):
    from drugpaper.skills.figure import sar_record

    _, _, bundle, _ = figure_env
    ctx = await bundle.context_for(
        ToolContext(user_id="default", document_id="doc_chem", run_id="r", trace_id="t"),
        "doc_chem/r1/p000.g000",
    )
    ctx.materials.target["children"] = [{"ref": "scaffold", "attributes": {"reading_index": 0}}]
    ctx.materials.target["structures"] = [
        {"node_ref": "scaffold", "is_markush": True, "e_smiles_raw": "*CC<sep><a>0:R</a>"}
    ]
    result = await sar_record(
        {
            "grid": {
                "orientation": "compounds_as_rows",
                "scaffold_mol_id": 0,
                "header_rows": [[{"text": "Compound"}, {"text": "R"}, {"text": "EC50 (nM)"}]],
                "rows": [[{"text": "1"}, {"mol_id": 9}, {"text": "8"}]],
            }
        },
        ctx,
    )
    p = result.artifacts[0].payload
    assert p["status"] == "needs_review" and p["assembly_skipped"] == "unresolved_substituent"
    assert p["rows"][0]["properties"][0]["value"] == 8
    assert not result.fact_proposals


async def test_figure_report_payload_keeps_sar_assembly(figure_env, monkeypatch):
    sv, _, _, _ = figure_env
    sar = {"status": "completed", "rows": [{"label": "1", "products": ["CC"], "properties": []}]}

    async def run_skill(self, state, name, inputs, **kwargs):
        if name == "sar_record":
            return sar
        if name == "measurement_map":
            return {"measurements": []}
        assert name == "mol_backfill"
        return {"payload": {"molecule_inventory": []}, "coverage": {}, "image_products": [], "errors": []}

    monkeypatch.setattr(FigureAgent, "_run_skill", run_skill)
    state = {
        "extraction": {"header_rows": [], "rows": []},
        "classification": {"image_type": "sar_table"},
        "material": {},
    }
    out = await FigureAgent(sv)._post(state)
    assert out["backfilled_extraction"]["sar_records"] == sar
    assert "sar_records" not in out["extraction"]


async def test_property_failure_preserves_completed_structural_backfill(figure_env, monkeypatch):
    sv, _, _, _ = figure_env
    filled = {"molecule_inventory": [], "molecules": [{"mol_id": 0, "esmiles": "CCO"}]}

    async def run_skill(self, state, name, inputs, **kwargs):
        if name == "mol_backfill":
            return {"payload": filled, "coverage": {"resolved_count": 1}, "image_products": [], "errors": []}
        assert name == "chart_measurements"
        raise ValueError("bad calibration")

    monkeypatch.setattr(FigureAgent, "_run_skill", run_skill)
    state = {
        "extraction": {"x_axis": {}, "molecules": [{"mol_id": 0}]},
        "classification": {"image_type": "data_chart"},
        "material": {},
    }
    with pytest.raises(ValueError, match="bad calibration"):
        await FigureAgent(sv)._post(state)
    assert state["backfilled_extraction"]["molecules"][0]["esmiles"] == "CCO"
    assert state["pre_backfill_extraction"]["molecules"][0] == {"mol_id": 0}


def test_review_cannot_replace_inventory_list_with_a_wrapper_object():
    from drugpaper.agents.figure.remediation import apply_review_fix
    from drugpaper.facts.figure_models import ReviewResult

    original = {"status": "ok", "molecule_inventory": []}
    review = ReviewResult.model_validate(
        {
            "items": [
                {
                    "dimension": "structure",
                    "field_path": "molecule_inventory",
                    "verdict": "incorrect",
                    "suggested_value": {"boxes": []},
                }
            ]
        }
    )
    with pytest.raises(ValueError):
        apply_review_fix(original, review)
    assert original == {"status": "ok", "molecule_inventory": []}
