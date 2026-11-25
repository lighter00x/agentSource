from __future__ import annotations

import copy

import httpx2
import pymupdf
import pytest
from sqlalchemy import select

from drugpaper.config.settings import Settings
from drugpaper.detection.models import RawDetection
from drugpaper.detection.service import MoleculeDetectionService
from drugpaper.domain.geometry import BBox, Geometry, Region
from drugpaper.domain.nodes import DocumentNode, IRDocument, PageInfo
from drugpaper.facts.backfill import backfill
from drugpaper.facts.legacy_molecule import Molecule
from drugpaper.facts.projector import project
from drugpaper.gateway.molparser import MolParserClient
from drugpaper.persistence.models import ArtifactRow, CompoundRow, NodeRow, StructureCandidateRow
from drugpaper.persistence.repos.artifacts import create_version
from drugpaper.persistence.repos.nodes import load_ir, save_ir
from drugpaper.services import Services
from drugpaper.skills.protocol import FactProposal, SkillResult
from drugpaper.tools.protocol import ToolContext
from tests.unit.detection.test_service import FakeBackend


@pytest.fixture
async def molecule_env(tmp_path):
    from drugpaper.persistence.models import Document

    sv = Services.from_settings(
        Settings(
            _env_file=None,
            database_url=f"sqlite+aiosqlite:///{tmp_path}/db",
            blob_root=tmp_path / "blobs",
            skills_root=tmp_path / "skills",
            redis_url="",
            crop_dpi=300,
        )
    )
    await sv.db.create_all()
    pdf = pymupdf.open()
    pdf.new_page(width=100, height=100)
    pdf.new_page(width=100, height=100)
    sha = await sv.store.put(pdf.tobytes())
    pdf.close()
    regions = [Region(page=i, bbox=BBox(x0=0.1, y0=0.1, x1=0.9, y1=0.9)) for i in range(2)]
    async with sv.db.session() as session:
        session.add(Document(id="doc_chem", user_id="default", sha256=sha))
        await session.flush()
        await save_ir(
            session,
            IRDocument(
                document_id="doc_chem",
                pages=[PageInfo(page=i, width_pt=100, height_pt=100) for i in range(2)],
                nodes=[
                    DocumentNode(
                        document_id="doc_chem",
                        revision=1,
                        local_id="p000.g000",
                        node_type="table",
                        geometry=Geometry(regions=regions),
                    ),
                    DocumentNode(
                        document_id="doc_chem",
                        revision=1,
                        local_id="p000.b000",
                        node_type="paragraph",
                        text_norm="compound 26 [UCM-1306] was prepared.",
                    ),
                ],
            ),
        )
    backend = FakeBackend(
        boxes=[RawDetection(bbox=(40, 70, 140, 210), confidence=0.9, class_id=0, class_name="molecule")]
    )
    sv._detector = MoleculeDetectionService(backend, sv.settings)
    raw = " CCO<sep> "

    def handler(request):
        if request.url.path == "/health":
            return httpx2.Response(200, json={"queue_size": 0, "queue_maxsize": 10})
        count = request.content.count(b'name="files"')
        return httpx2.Response(
            200, json={"results": [{"input_index": i, "esmi": raw} for i in reversed(range(count))]}
        )

    sv._molparser = MolParserClient(
        "http://mock", http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(handler))
    )
    bundle = await sv.skills()

    async def run(name, ref="doc_chem/r1/p000.g000", inputs=None, force=False):
        ctx = await bundle.context_for(
            ToolContext(user_id="default", document_id="doc_chem", run_id="parent", trace_id="trace"), ref
        )
        result = await bundle.runtime.run(name, ref, inputs or {}, ctx, force=force)
        assert result.status == "completed", result.error
        return result

    yield sv, backend, bundle, run
    await sv.aclose()


async def test_cross_page_detection_projection_id_stability_and_removed(molecule_env):
    sv, backend, _, run = molecule_env
    await run("molecule_detect")
    async with sv.db.session() as session:
        ir = await load_ir(session, "doc_chem")
    nodes = ir.of_type("molecule")
    assert len(nodes) == 2 and {n.page for n in nodes} == {0, 1}
    assert [n.attributes["reading_index"] for n in nodes] == [0, 1]
    assert ir.get("p000.g000").children == [n.local_id for n in nodes]
    await run("molecule_detect", force=True)
    async with sv.db.session() as session:
        again = await load_ir(session, "doc_chem")
    assert [n.local_id for n in again.of_type("molecule")] == [n.local_id for n in nodes]
    # 改变图像或权重才绕过 Tool 内容缓存；清掉测试缓存模拟检测器更新。
    sv._detector._cache.clear()
    backend.predict = lambda *args, **kwargs: []
    empty = await run("molecule_detect", force=True)
    assert empty.artifacts[0].payload["status"] == "needs_fallback"
    async with sv.db.session() as session:
        removed = await load_ir(session, "doc_chem")
    assert all(n.status == "removed" for n in removed.of_type("molecule"))
    assert removed.get("p000.g000").children == []


async def test_recognize_raw_roundtrip_rerun_backfill_and_compound_alias(molecule_env):
    sv, _, _, run = molecule_env
    detection = await run("molecule_detect")
    nodes = detection.artifacts[0].payload["boxes"]
    refs = [f"doc_chem/r1/{n['local_id']}" for n in nodes]
    for ref in refs:
        await run("molecule_recognize", ref)
    cached = await run("molecule_recognize", refs[0])
    assert cached.cached
    async with sv.db.session() as session:
        structures = list(
            (
                await session.execute(
                    select(StructureCandidateRow).where(StructureCandidateRow.is_active.is_(True))
                )
            ).scalars()
        )
    assert len(structures) == 2 and all(s.payload["e_smiles_raw"] == " CCO<sep> " for s in structures)
    await run("molecule_recognize", refs[0], force=True)
    async with sv.db.session() as session:
        active = list(
            (
                await session.execute(
                    select(StructureCandidateRow).where(StructureCandidateRow.is_active.is_(True))
                )
            ).scalars()
        )
    assert len(active) == 2 and structures[1].id in {s.id for s in active}
    original = {"molecules": [{"mol_id": 0, "reference": "compound 26"}]}
    before = copy.deepcopy(original)
    filled = await run("mol_backfill", inputs={"extraction_payload": original, "image_type": 10})
    assert (
        original == before
        and filled.artifacts[0].payload["payload"]["molecules"][0]["esmiles"] == " CCO<sep> "
    )
    await run("compound_resolve", "doc_chem")
    async with sv.db.session() as session:
        compounds = list((await session.execute(select(CompoundRow))).scalars())
    assert len(compounds) == 1 and compounds[0].primary_label == "26"
    assert compounds[0].payload["aliases"] == ["UCM-1306"] and compounds[0].accepted_structure_id
    marked = await run("som_render")
    assert (await run("som_render")).cached
    assert len(marked.artifacts[0].payload["images"]) == 2
    assert all([await sv.store.exists(i["blob_ref"]) for i in marked.artifacts[0].payload["images"]])
    label = await run("label_bind")
    assert len(label.artifacts[0].payload["candidates"]) == 2


async def test_row_assembly_persists_sar_facts_and_positional_skip(molecule_env):
    sv, _, _, run = molecule_env
    result = await run(
        "markush_assemble",
        inputs={
            "caption": "C1CCCCC1*<sep><a>6:R</a>",
            "rows": [{"label": "3", "values": {"R": "Me"}}, {"label": "4", "values": {"CHO position": "4"}}],
        },
    )
    payload = result.artifacts[0].payload
    assert payload["rows"][0]["products"] == ["CC1CCCCC1"]
    assert payload["rows"][1]["error_code"] == "positional_variable"
    await run("compound_resolve", "doc_chem")
    async with sv.db.session() as session:
        compounds = list((await session.execute(select(CompoundRow))).scalars())
    assert {c.primary_label for c in compounds} == {"3", "4", "26"}


async def test_projection_failure_rolls_back_artifact_and_owner_guard(molecule_env):
    sv, _, bundle, _ = molecule_env
    ctx = await bundle.context_for(
        ToolContext(user_id="default", run_id="r", trace_id="t"), "doc_chem/r1/p000.g000"
    )
    with pytest.raises(PermissionError):
        await bundle.context_for(ToolContext(user_id="other", run_id="r", trace_id="t"), ctx.target_ref)
    with pytest.raises(PermissionError):
        async with sv.db.session() as session:
            artifact = await create_version(
                session,
                document_id="doc_chem",
                artifact_type="ocsr_result",
                target_ref=ctx.target_ref,
                work_unit_key="bad",
                payload={},
            )
            result = SkillResult(
                fact_proposals=[
                    FactProposal(
                        fact_type="molecule_update",
                        payload={"node_ref": "doc_other/r1/p000.g000.m0", "attributes": {}},
                    )
                ]
            )
            await project(session, artifact.id, result, ctx, "molecule_recognize")
    async with sv.db.session() as session:
        assert (
            await session.execute(select(ArtifactRow).where(ArtifactRow.work_unit_key == "bad"))
        ).scalar_one_or_none() is None


def test_plural_backfill_errors_are_precise_and_nonmutating():
    original = {"molecules": [{"mol_ids": [0, 1, True, 99]}]}
    filled = backfill("doc/r1/n", original, {0: Molecule("m0", "CC"), 1: Molecule("m1", "CO")}, 10)
    assert original == {"molecules": [{"mol_ids": [0, 1, True, 99]}]}
    assert filled["payload"]["molecules"][0]["esmiles_list"] == ["CC", "CO", None, None]
    assert [e["field_path"] for e in filled["errors"]] == [
        "molecules[0].mol_ids[2]",
        "molecules[0].mol_ids[3]",
    ]
    assert all(e["available_mol_ids"] == [0, 1] for e in filled["errors"])


async def test_invalid_recognition_marks_node_review(molecule_env):
    sv, _, _, run = molecule_env
    boxes = (await run("molecule_detect")).artifacts[0].payload["boxes"]

    def handler(request):
        if request.url.path == "/health":
            return httpx2.Response(200, json={})
        return httpx2.Response(200, json={"results": [{"input_index": 0, "esmi": "invalid"}]})

    await sv._molparser.aclose()
    sv._molparser = MolParserClient(
        "http://mock", http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(handler))
    )
    ref = f"doc_chem/r1/{boxes[0]['local_id']}"
    result = await run("molecule_recognize", ref, force=True)
    assert result.artifacts[0].status == "needs_review"
    async with sv.db.session() as session:
        node = await session.get(NodeRow, ("doc_chem", 1, boxes[0]["local_id"]))
        assert node.status == "needs_review"
    assert not result.artifacts[0].payload["structures"][0]["validation"]["valid"]


async def test_compound_read_api_tool_citations_and_owner_guard(molecule_env):
    from drugpaper.api.compounds import compound_detail, list_compounds
    from drugpaper.gateway.base import ToolCall
    from drugpaper.tools.builtin.compounds import make_compound_tools
    from drugpaper.tools.registry import ToolRegistry

    sv, _, _, run = molecule_env
    await run(
        "markush_assemble",
        inputs={"caption": "C1CCCCC1*<sep><a>6:R</a>", "rows": [{"label": "26", "values": {"R": "Me"}}]},
    )
    await run("compound_resolve", "doc_chem")
    async with sv.db.session() as session:
        rows = await list_compounds("doc_chem", session, 0, 50)
        compound = next(c for c in rows if c["primary_label"] == "26")
        detail = await compound_detail("doc_chem", compound["compound_id"], session)
        assert len(detail["structures"]) == 1 and detail["conflicts"] == []
    registry = ToolRegistry(make_compound_tools(sv))
    result = await registry.invoke(
        ToolContext(document_id="doc_chem", run_id="r", trace_id="t"),
        ToolCall(id="c", name="list_compounds", arguments={"query": "UCM-1306"}),
    )
    assert result.ok and result.data["total"] == 1 and result.refs == ["doc_chem/r1/p000.g000"]
    forbidden = await registry.invoke(
        ToolContext(user_id="other", document_id="doc_chem", run_id="r", trace_id="t"),
        ToolCall(id="c", name="list_compounds", arguments={}),
    )
    assert not forbidden.ok and forbidden.error == "E_FORBIDDEN"


async def test_overlay_coordinates_and_pipeline_work_units(molecule_env, tmp_path):
    from PIL import Image

    from drugpaper.domain.geometry import norm_to_px
    from drugpaper.orchestration.molecule_pipeline import MoleculePipeline
    from scripts.debug_molecule_overlay import overlay

    sv, _, _, _ = molecule_env
    result = await MoleculePipeline(sv).run("doc_chem")
    recognized = [r for r in result["runs"] if r["skill"] == "molecule_recognize"]
    assert len(recognized) == 2 and all(".m" in r["target_ref"] for r in recognized)
    paths = await overlay(sv, "doc_chem", tmp_path / "overlay")
    assert len(paths) == 2
    async with sv.db.session() as session:
        ir = await load_ir(session, "doc_chem")
    image = Image.open(paths[0])
    node = next(n for n in ir.of_type("molecule") if n.page == 0)
    x0, y0, _, _ = norm_to_px(node.geometry.regions[0].bbox, *image.size)
    assert image.getpixel((x0, y0)) == (255, 0, 0)


async def test_constructed_sar_backfill_assembly_resolve_complete_chain(molecule_env):
    sv, _, _, run = molecule_env
    boxes = (await run("molecule_detect")).artifacts[0].payload["boxes"]
    raw = "C1CCCCC1*<sep><a>6:R</a>"

    def handler(request):
        if request.url.path == "/health":
            return httpx2.Response(200, json={})
        return httpx2.Response(200, json={"results": [{"input_index": 0, "esmi": raw}]})

    await sv._molparser.aclose()
    sv._molparser = MolParserClient(
        "http://mock", http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(handler))
    )
    await run("molecule_recognize", f"doc_chem/r1/{boxes[0]['local_id']}", force=True)
    extraction = {
        "table_metadata": {"scaffold": {"mol_id": 0}},
        "content": [{"reference": "101", "substituents": {"R": "Me"}}],
    }
    filled = (
        (await run("mol_backfill", inputs={"image_type": 3, "extraction_payload": extraction}))
        .artifacts[0]
        .payload["payload"]
    )
    assembly = await run(
        "markush_assemble",
        inputs={
            "caption": filled["table_metadata"]["scaffold"]["esmiles"],
            "rows": [
                {"label": filled["content"][0]["reference"], "values": filled["content"][0]["substituents"]}
            ],
        },
    )
    assert assembly.artifacts[0].payload["products"] == ["CC1CCCCC1"]
    resolved = await run("compound_resolve", "doc_chem")
    compound = next(c for c in resolved.artifacts[0].payload["compounds"] if c["primary_label"] == "101")
    assert compound["accepted_structure_id"]
