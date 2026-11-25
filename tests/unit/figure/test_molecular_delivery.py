import copy

import pytest

from drugpaper.facts.molecular_delivery import deliver
from drugpaper.skills.prompt_materials import project_target

BOXES = [
    {
        "mol_id": 0,
        "node_ref": "doc/r1/fig.mol000",
        "segment": 0,
        "bbox": {"x0": 0.1, "y0": 0.1, "x1": 0.4, "y1": 0.7},
    },
    {
        "mol_id": 1,
        "node_ref": "doc/r1/fig.mol001",
        "segment": 1,
        "bbox": {"x0": 0.5, "y0": 0.2, "x1": 0.9, "y1": 0.8},
    },
]
STRUCTURES = [
    {"node_ref": b["node_ref"], "structure_id": f"str{i}", "e_smiles_raw": raw, "validation": {"valid": True}}
    for i, (b, raw) in enumerate(zip(BOXES, ["CCO", "CCN"], strict=True))
]


async def test_prepare_preserves_document_pages_for_cross_page_image_segments(tmp_path):
    import base64
    import io
    from types import SimpleNamespace

    from PIL import Image

    from drugpaper.agents.figure.graph import FigureAgent
    from drugpaper.gateway.base import ImagePart
    from drugpaper.storage.localfs import LocalFsBlobStore

    buffer = io.BytesIO()
    Image.new("RGB", (2, 2), "white").save(buffer, format="PNG")
    part = ImagePart(data_b64=base64.b64encode(buffer.getvalue()).decode(), mime="image/png")
    ctx = SimpleNamespace(
        materials=SimpleNamespace(images=[part, part], target={"regions": [{"page": 4}, {"page": 9}]})
    )

    async def context(state):
        return None, ctx

    async def run_skill(state, name):
        return {"images": [{"parent_region_index": 1, "blob_ref": "numbered"}]}

    async def material(state):
        return {}

    store = LocalFsBlobStore(tmp_path)
    agent = SimpleNamespace(
        _ctx=context,
        _run_skill=run_skill,
        _material=material,
        services=SimpleNamespace(store_for=lambda user: store),
    )
    state = {"user_id": "default", "chain": ["som_render"]}
    await FigureAgent._prepare(agent, state)
    assert [(item["segment"], item["page"]) for item in state["image_products"]] == [(0, 4), (1, 9), (1, 9)]


@pytest.mark.parametrize(
    "payload",
    [
        {"header_rows": [[{"mol_id": 0}]], "rows": [[{"text": "50"}, {"mol_id": 1}]]},
        {"scaffold_mol_id": 0, "rows": [[{"mol_id": 1}]]},
        {"series": [{"label": "curve", "mol_id": 0}], "molecules": [{"mol_id": 1}]},
        {"base_info": {"mol_id": 0}, "optimization_steps": [{"extra": [{"mol_id": 1}]}]},
        {"pathways": [{"transformations": [{"from": [{"mol_id": 0}], "to": {"mol_id": 1}}]}]},
        {"targets": [{"ligand": {"mol_id": 0}}], "molecules": [{"mol_id": 1}]},
        {"protac": {"warhead": {"mol_id": 0}, "ligase": {"mol_id": 1}}},
        {"pharmacophores": [{"mol_ids": [0, 1]}]},
        {"molecules": [{"mol_id": 0, "substituents": {"R": 1, "R2": "Me"}}]},
        {"panels": [{"molecules": [{"mol_id": 0}, {"mol_id": 1}]}]},
    ],
)
def test_all_image_shapes_bind_without_mutation(payload):
    original = copy.deepcopy(payload)
    r = deliver(payload, BOXES, STRUCTURES)
    assert not r["errors"]
    assert r["coverage"]["resolved_count"] == 2
    assert {b["e_smiles_raw"] for b in r["bindings"]} == {"CCO", "CCN"}
    assert {b["segment"] for b in r["bindings"]} == {0, 1}
    assert payload == original and r["original"] == original
    assert "CCO" not in str(original)


@pytest.mark.parametrize(
    "mode",
    ["missing", "invalid", "duplicate", "unreferenced", "bool", "unknown", "smiles_injection", "empty"],
)
def test_wrong_binding_cannot_pass(mode):
    structures = copy.deepcopy(STRUCTURES)
    data = {"molecules": [{"mol_id": 0}, {"mol_id": 1}]}
    if mode == "missing":
        structures.pop()
    if mode == "invalid":
        structures[1]["validation"]["valid"] = False
    if mode == "duplicate":
        structures.append(copy.deepcopy(structures[1]))
    if mode == "unreferenced":
        data["molecules"].pop()
    if mode == "bool":
        data["molecules"][1]["mol_id"] = True
    if mode == "unknown":
        data["molecules"][1]["mol_id"] = 99
    if mode == "smiles_injection":
        data["molecules"][0]["esmiles"] = "WRONG"
    if mode == "empty":
        structures[1]["e_smiles_raw"] = ""
    r = deliver(data, BOXES, structures)
    assert r["errors"] and r["coverage"]["status"] == "needs_review"
    assert "WRONG" not in str(r["payload"])


def test_exclusion_requires_evidence_and_is_pending_visual_review():
    data = {
        "molecules": [{"mol_id": 0}],
        "molecule_inventory": [
            {"mol_id": 1, "disposition": "excluded", "evidence_text": "box contains a legend, not bonds"}
        ],
    }
    r = deliver(data, BOXES, STRUCTURES)
    assert not r["errors"]
    assert r["coverage"]["items"][1]["status"] == "excluded_pending_review"
    data["molecule_inventory"][0]["evidence_text"] = ""
    assert deliver(data, BOXES, STRUCTURES)["errors"]


def test_model_material_never_discloses_recognized_smiles():
    target = {
        "structures": STRUCTURES,
        "children": [
            {
                "attributes": {"reading_index": 0},
                "geometry": {"local_bbox": BOXES[0]["bbox"]},
                "status": "active",
            }
        ],
    }
    for name in [
        "table_extract",
        "chart_digitize",
        "scheme_extract",
        "structures_extract",
        "common_extract",
        "molecular_docking_extract",
        "protac_architecture_extract",
        "metabolic_pathway_extract",
        "pharmacophore_model_extract",
    ]:
        view = project_target(name, target)
        assert "CCO" not in str(view) and "CCN" not in str(view)
        assert view["boxes"][0]["mol_id"] == 0


async def test_universal_skill_preserves_original_all_four_images_and_delivery(figure_env):
    sv, _, _, run = figure_env
    detected = await run("molecule_detect")
    local_id = detected.artifacts[0].payload["boxes"][0]["local_id"]
    await run("molecule_recognize", ref=f"doc_chem/r1/{local_id}")
    payload = {"series": [{"label": "26", "mol_id": 0}], "molecule_inventory": []}
    result = await run("mol_backfill", inputs={"contract": "universal", "extraction_payload": payload})
    assert result.status == "completed", result.error
    artifact = result.artifacts[0].payload
    assert not artifact["errors"]
    assert artifact["original"] == payload
    assert artifact["payload"]["series"][0]["mol_id"] == 0
    assert artifact["payload"]["series"][0]["esmiles"].strip() == "CCO<sep>"
    assert {p["stage"] for p in artifact["image_products"]} == {
        "original",
        "before_backfill",
        "after_backfill",
        "final",
    }
    for p in artifact["image_products"]:
        assert (await sv.store.get(p["blob_ref"])).startswith(b"\x89PNG")


async def test_long_internal_tool_delivery_is_complete_while_model_output_is_bounded():
    from drugpaper.gateway.base import ToolCall
    from drugpaper.tools.builtin.chem import make_chem_tools
    from drugpaper.tools.protocol import ToolContext
    from drugpaper.tools.registry import ToolRegistry

    registry = ToolRegistry(make_chem_tools())
    payload = {
        "rows": [[{"text": "preserve printed cell " * 20}] for _ in range(40)],
        "molecules": [{"mol_id": 0}, {"mol_id": 1}],
    }
    args = {"data": payload, "boxes": BOXES, "structures": STRUCTURES}
    ctx = ToolContext(user_id="one", run_id="run", trace_id="trace")
    call = ToolCall(id="test", name="mol.deliver", arguments=args)
    bounded = await registry.invoke(ctx, call)
    assert bounded.truncated
    full = await registry.invoke(ctx, call, truncate=False)
    assert not full.truncated and full.data["coverage"]["resolved_count"] == 2
    assert full.data["original"] == payload


async def test_concurrent_users_keep_same_box_ids_and_distinct_structures_isolated():
    import asyncio

    from drugpaper.gateway.base import ToolCall
    from drugpaper.tools.builtin.chem import make_chem_tools
    from drugpaper.tools.protocol import ToolContext
    from drugpaper.tools.registry import ToolRegistry

    registry = ToolRegistry(make_chem_tools())

    async def one(user):
        boxes = copy.deepcopy(BOXES)
        structures = copy.deepcopy(STRUCTURES)
        raw = "CCO" if user % 2 == 0 else "CCN"
        for b, s in zip(boxes, structures, strict=True):
            b["node_ref"] = s["node_ref"] = f"doc_{user}/r1/fig.m{b['mol_id']}"
            s["e_smiles_raw"] = raw
        result = await registry.invoke(
            ToolContext(user_id=str(user), run_id="r", trace_id="t"),
            ToolCall(
                id="fill",
                name="mol.deliver",
                arguments={
                    "data": {"molecules": [{"mol_ids": [0, 1]}]},
                    "boxes": boxes,
                    "structures": structures,
                },
            ),
            truncate=False,
        )
        assert not result.data["errors"]
        assert result.data["payload"]["molecules"][0]["esmiles_list"] == [raw, raw]
        assert all(b["node_ref"].startswith(f"doc_{user}/") for b in result.data["bindings"])

    await asyncio.gather(*(one(i) for i in range(20)))


def test_invalid_chemical_graph_is_rejected_even_if_metadata_claims_valid():
    structures = copy.deepcopy(STRUCTURES)
    structures[0]["e_smiles_raw"] = "not_a_smiles"
    result = deliver({"molecules": [{"mol_id": 0}, {"mol_id": 1}]}, BOXES, structures)
    assert any(e["code"] == "invalid_structure" for e in result["errors"])
    assert result["coverage"]["status"] == "needs_review"


async def test_structure_failure_preserves_actual_source_and_numbered_input(figure_env):
    from drugpaper.agents.figure.graph import FigureAgent
    from drugpaper.gateway.base import SchemaViolation
    from drugpaper.gateway.task_router import TaskRouter
    from tests.unit.figure.test_agent import FigureFake

    sv, _, bundle, _ = figure_env

    class BrokenStructure(FigureFake):
        async def chat(self, req, *, task="default"):
            if task == "figure_structure":
                raise SchemaViolation("invalid extraction", errors=["missing grid"])
            return await super().chat(req, task=task)

    fake = BrokenStructure()
    fake.router = TaskRouter.from_file(sv.settings.models_config_path)
    bundle.models = fake
    report = await FigureAgent(sv).run("doc_chem/r1/p000.g000")
    assert report.status == "failed" and not report.extraction
    assert {p["stage"] for p in report.image_products} == {"original", "model_input_numbered"}
    for product in report.image_products:
        assert (await sv.store.get(product["blob_ref"])).startswith(b"\x89PNG")


@pytest.mark.parametrize("field,value", [("mol_id", True), ("mol_id", -1), ("row", False), ("column", True)])
def test_compact_wire_never_coerces_boolean_or_negative_structure_indices(field, value):
    from pydantic import ValidationError

    from drugpaper.facts.table_transcription import CellDetail

    data = {"row": 0, "column": 0, "mol_id": 0}
    data[field] = value
    with pytest.raises(ValidationError):
        CellDetail.model_validate(data)


async def test_model_correct_review_cannot_certify_unclear_fallback():
    from drugpaper.agents.figure.accounting import CallAccounting
    from drugpaper.agents.figure.graph import FigureAgent

    agent = object.__new__(FigureAgent)
    state = {
        "document_id": "doc",
        "node_ref": "doc/r1/fig",
        "user_id": "one",
        "run_id": "r",
        "mode": "full",
        "extraction": {"status": "unclear", "molecules": []},
        "classification": {"image_type": "other"},
        "checks": [],
        "checklist": [],
        "review": {"items": []},
        "accounting": CallAccounting().model_dump(),
        "artifact_ids": [],
        "trace": [],
    }
    result = await agent._report(state)
    assert result["report"]["status"] == "needs_review"
    assert any("unusable/unclear" in text for text in result["report"]["open_questions"])


def test_single_and_plural_bindings_keep_separate_field_semantics():
    result = deliver({"molecules": [{"mol_id": 0, "mol_ids": [1]}]}, BOXES, STRUCTURES)
    entity = result["payload"]["molecules"][0]
    assert entity["esmiles"] == "CCO" and entity["esmiles_list"] == ["CCN"]
    assert not result["errors"]


def test_failed_plural_reference_preserves_original_list_positions():
    result = deliver({"molecules": [{"mol_ids": [0, 99, 1]}]}, BOXES, STRUCTURES)
    assert result["payload"]["molecules"][0]["esmiles_list"] == ["CCO", None, "CCN"]
    assert result["coverage"]["status"] == "needs_review"
    assert any(e.get("field_path") == "$.molecules[0].mol_ids[1]" for e in result["errors"])


async def test_compound_label_is_not_projected_onto_substituent_fragments(molecule_env):
    from sqlalchemy import select

    from drugpaper.persistence.models import LabelClaimRow

    sv, _, _, run = molecule_env
    detected = await run("molecule_detect")
    nodes = detected.artifacts[0].payload["boxes"]
    for node in nodes:
        await run("molecule_recognize", ref=f"doc_chem/r1/{node['local_id']}")
    await run(
        "mol_backfill",
        inputs={
            "contract": "universal",
            "extraction_payload": {"molecules": [{"reference": "26", "mol_id": 0, "substituents": {"R": 1}}]},
        },
    )
    async with sv.db.session() as session:
        claims = list((await session.execute(select(LabelClaimRow))).scalars())
    assert len(claims) == 1
    assert claims[0].payload["node_ref"] == f"doc_chem/r1/{nodes[0]['local_id']}"


@pytest.mark.parametrize("inventory", [{"boxes": []}, ["bad entry"]])
def test_invalid_inventory_returns_explicit_failure_instead_of_crashing(inventory):
    result = deliver({"molecule_inventory": inventory}, BOXES, STRUCTURES)
    assert result["errors"] and result["coverage"]["status"] == "needs_review"
    assert isinstance(result["payload"]["molecule_inventory"], list)
