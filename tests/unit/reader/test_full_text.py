from __future__ import annotations

import copy
import hashlib
import io
import json
import zipfile

import httpx
import pytest
from PIL import Image
from sqlalchemy import select

from drugpaper.agents.lookup.batch import DocLookup
from drugpaper.agents.lookup.models import LookupQuestion
from drugpaper.agents.reader.graph import ReaderAgent
from drugpaper.agents.reader.notes import NoteDelta, Observation, ReaderNotes, compact, merge_delta
from drugpaper.agents.reader.queue import enqueue, should_flush
from drugpaper.api.app import create_app
from drugpaper.chem.change_classifier import classify
from drugpaper.chem.diff import fragment_changes, replace_regions, virtual_intermediates
from drugpaper.chem.normalize import canonical
from drugpaper.config.settings import Settings
from drugpaper.domain.geometry import BBox, Geometry, Region
from drugpaper.domain.nodes import DocumentMeta, DocumentNode, IRDocument, PageInfo
from drugpaper.evals.silver import compare, evaluate
from drugpaper.exporters.package import create_export
from drugpaper.facts.full_text import load_context
from drugpaper.facts.paths import ClaimDraft, PathProposal, ProposedStep, build_steps
from drugpaper.facts.text_measurements import project_text_measurements
from drugpaper.orchestration.full_text_pipeline import FullTextPipeline
from drugpaper.persistence.models import (
    ArtifactRow,
    AssayRow,
    CompoundRow,
    Document,
    FigureReportRow,
    MeasurementRow,
    ReaderNoteRow,
    StructureCandidateRow,
)
from drugpaper.persistence.repos.nodes import save_ir
from drugpaper.profiles.projector import project
from drugpaper.services import Services
from drugpaper.validators.path import validate_path
from drugpaper.validators.projection import validate_projection
from tests.fakes.fake_llm import FakeLLM, text


@pytest.fixture
async def full_env(tmp_path):
    settings = Settings(
        _env_file=None,
        database_url=f"sqlite+aiosqlite:///{tmp_path}/full.db",
        blob_root=tmp_path / "blobs",
        skills_root=tmp_path / "skills",
        runs_dir=tmp_path / "runs",
        skills_watch_enabled=False,
    )
    sv = Services.from_settings(settings)
    await sv.db.create_all()

    def node(local, page, content, role="results", subtype=None, kind="paragraph", label=None):
        return DocumentNode(
            document_id="doc_full",
            revision=1,
            local_id=local,
            node_type=kind,
            subtype=subtype,
            reading_order=len(nodes),
            label=label,
            text_raw=content,
            text_norm=content,
            section_role=role,
            geometry=Geometry(regions=[Region(page=page, bbox=BBox(x0=0.1, y0=0.1, x1=0.8, y1=0.8))]),
        )

    nodes = []
    nodes.append(node("title", 0, "Optimization of a model drug", subtype="title"))
    nodes.append(node("authors", 0, "A. Scientist", subtype="authors"))
    nodes.append(
        node("abstract", 0, "Compound 1 was optimized to compound 26 with improved activity.", "abstract")
    )
    nodes.append(
        node(
            "results",
            1,
            "Compound 1 was converted to compound 26 by N-demethylation. Compound 26 has hERG inhibition of 2%.",
            "results",
        )
    )
    nodes.append(node("caption", 1, "Figure 1. Activity of compounds 1 and 26.", kind="caption"))
    nodes.append(node("chart", 1, "", kind="figure", label="Figure 1"))
    nodes.append(node("experimental", 2, "N-methylaniline (26). Purified compound.", "experimental"))
    nodes.append(node("references", 3, "References only.", "references"))
    ir = IRDocument(
        document_id="doc_full",
        pages=[PageInfo(page=p, width_pt=600, height_pt=800) for p in range(4)],
        nodes=nodes,
        meta=DocumentMeta(title="Optimization of a model drug", authors=["A. Scientist"]),
    )
    png = io.BytesIO()
    Image.new("RGB", (20, 20), "white").save(png, format="PNG")
    blob = await sv.store.put(png.getvalue(), "image/png")
    nodes[5].attributes["image_blob"] = blob
    async with sv.db.session() as s:
        s.add(Document(id="doc_full", sha256="fullsha", status="ready", active_revision=1))
        await s.flush()
        await save_ir(s, ir)
        for n in nodes:
            if n.node_type != "paragraph" or n.section_role == "references":
                continue
            payload = {
                "status": "completed",
                "source_text_sha256": hashlib.sha256(n.text_raw.encode()).hexdigest(),
                "compound_mentions": [],
                "measurements": [],
                "optimization_statements": [],
            }
            s.add(
                ArtifactRow(
                    id="t1_" + n.local_id,
                    document_id="doc_full",
                    artifact_type="text_extraction",
                    target_ref=n.ref,
                    work_unit_key="t1_" + n.local_id,
                    payload=payload,
                )
            )
        evidence = [{"node_id": nodes[3].ref, "role": "primary", "char_span": [0, len(nodes[3].text_raw)]}]
        s.add(
            ArtifactRow(
                id="manual_art",
                document_id="doc_full",
                artifact_type="structures",
                target_ref=nodes[3].ref,
                work_unit_key="manual_art",
                payload={},
            )
        )
        await s.flush()
        for cid, label, smiles in [("c1", "1", "CN(C)c1ccccc1"), ("c26", "26", "CNc1ccccc1")]:
            structure = {
                "structure_id": "s" + cid,
                "compound_id": cid,
                "source": "manual",
                "node_ref": nodes[3].ref,
                "e_smiles_raw": smiles,
                "smiles_core": smiles,
                "is_markush": False,
                "inchikey": canonical(smiles).inchikey,
                "evidence": evidence,
            }
            s.add(
                CompoundRow(
                    id=cid,
                    document_id="doc_full",
                    primary_label=label,
                    accepted_structure_id="s" + cid,
                    payload={
                        "compound_id": cid,
                        "document_id": "doc_full",
                        "primary_label": label,
                        "aliases": [],
                        "evidence": evidence,
                    },
                )
            )
            s.add(
                StructureCandidateRow(
                    id="s" + cid,
                    document_id="doc_full",
                    artifact_id="manual_art",
                    target_ref=nodes[3].ref,
                    node_ref=nodes[3].ref,
                    compound_id=cid,
                    source="manual",
                    payload=structure,
                )
            )
            for metric, unit, value in [
                ("ic50", "nM", "20" if cid == "c1" else "2"),
                ("ec50", "nM", "50" if cid == "c1" else "5"),
                ("herg", "%", "20" if cid == "c1" else "2"),
            ]:
                mid = f"m{cid}_{metric}"
                aid = f"a{cid}_{metric}"
                s.add(
                    AssayRow(
                        id=aid,
                        document_id="doc_full",
                        artifact_id="manual_art",
                        target_ref=nodes[3].ref,
                        payload={"assay_id": aid, "species": "human", "conditions": {}, "evidence": evidence},
                    )
                )
                s.add(
                    MeasurementRow(
                        id=mid,
                        document_id="doc_full",
                        artifact_id="manual_art",
                        target_ref=nodes[3].ref,
                        payload={
                            "measurement_id": mid,
                            "document_id": "doc_full",
                            "compound_id": cid,
                            "subject_label": label,
                            "assay_id": aid,
                            "metric": metric,
                            "metric_label_raw": metric,
                            "value_raw": value,
                            "unit_raw": unit,
                            "status": "explicit",
                            "source_kind": "text",
                            "evidence": evidence,
                        },
                    )
                )
        report = {
            "document_id": "doc_full",
            "node_ref": nodes[5].ref,
            "mode": "full",
            "status": "ok",
            "image_type": "chart",
            "extraction": {"series": [{"label": "26", "points": [[0, 2], [1, 3]]}], "overlay_blob_ref": blob},
        }
        s.add(
            FigureReportRow(
                id="report",
                document_id="doc_full",
                node_ref=nodes[5].ref,
                mode="full",
                status="ok",
                input_fingerprint="test",
                payload=report,
            )
        )
    yield sv, ir
    await sv.aclose()


def proposal(ir, **kwargs):
    ref = ir.nodes[3].ref
    value = PathProposal(
        lead_compound_id="c1",
        final_compound_id="c26",
        main_route=["c1", "c26"],
        steps=[
            ProposedStep(
                from_id="c1",
                to_id="c26",
                declared_change_types=["n_dealkylation"],
                change_text="N-去甲基化",
                rationale="降低不良作用并改善活性",
                evidence_node_ids=[ref],
            )
        ],
        lead_problems=[ClaimDraft(text="活性及安全性有待改善", evidence_node_ids=[ref])],
        tags=["活性提升"],
        summary=ClaimDraft(text="通过去甲基化改善先导的活性和安全性", evidence_node_ids=[ref]),
    )
    return value.model_copy(update=kwargs)


def delta(ir, page):
    ref = ir.nodes[2 if page == 0 else 3].ref
    return NoteDelta(
        page=page, thesis_updates=[Observation(text="论文以活性优化为目标", page=page, evidence_nodes=[ref])]
    )


async def test_lookup_fast_and_shared_plan_cache(full_env):
    sv, ir = full_env
    ctx = await load_context(sv.db, ir.document_id)
    qs = [
        LookupQuestion(question=q)
        for q in [
            "26出现在哪？",
            "26的hERG值？",
            "Figure 1的图注？",
            "先导为什么优化？",
            "最终产物怎么得到？",
            "主要贡献是什么？",
        ]
    ]
    ref = ir.nodes[3].ref
    plan = {
        "actions": [{"question_id": i, "tool": "get_node", "args": {"node_id": ref}} for i in range(3, 6)]
    }
    answers = {
        "results": [
            {
                "question": qs[i].question,
                "answer": "去甲基化以改善活性。",
                "status": "found",
                "evidence": [{"node_id": ref}],
            }
            for i in range(3, 6)
        ]
    }
    llm = FakeLLM([text(json.dumps(plan)), text(json.dumps(answers, ensure_ascii=False))])
    lookup = DocLookup(sv, llm)
    result = await lookup.lookup(qs, ctx)
    assert result.model_calls == 2 and result.tool_calls == 6
    assert len(llm.requests) == 2 and all(r.status == "found" for r in result.results), result
    assert all(not req.tools for req in llm.requests)
    hit = await lookup.lookup([qs[0], qs[0]], ctx)
    assert hit.cache_hits == 1 and hit.model_calls == 0 and len(hit.results) == 2
    other = qs[3].model_copy(update={"question": "另外的问题"})
    bad = DocLookup(
        sv,
        FakeLLM(
            [
                text(json.dumps({"actions": [{"question_id": 0, "tool": "name_to_structure", "args": {}}]})),
                text(
                    json.dumps(
                        {
                            "results": [
                                {
                                    "question": other.question,
                                    "status": "found",
                                    "answer": "猜测",
                                    "evidence": [{"node_id": "other/r1/node"}],
                                }
                            ]
                        },
                        ensure_ascii=False,
                    )
                ),
            ]
        ),
    )
    out = await bad.lookup([other], ctx)
    assert out.tool_calls == 0 and out.results[0].status == "not_found"


async def test_lookup_scope_and_invalidation(full_env):
    sv, ir = full_env
    ctx = await load_context(sv.db, ir.document_id)
    with pytest.raises(PermissionError):
        await load_context(sv.db, ir.document_id, user_id="other")
    with pytest.raises(ValueError):
        await DocLookup(sv).lookup([LookupQuestion(question="x")] * 9, ctx)
    async with sv.db.session() as s:
        from drugpaper.persistence.models import NodeRow

        n = await s.get(NodeRow, (ir.document_id, 1, "results"))
        n.text_raw = "Changed text."
    with pytest.raises(ValueError, match="stale"):
        await DocLookup(sv).lookup([LookupQuestion(question="26出现在哪")], ctx)


async def test_reader_durable_resume_reread_and_pending(full_env):
    sv, ir = full_env
    llm = FakeLLM([text(delta(ir, 0).model_dump_json()), RuntimeError("interrupted")])
    out = await ReaderAgent(sv, llm).read(ir.document_id)
    assert out.status == "failed" and out.last_page == 0
    restored = ReaderAgent(sv, FakeLLM([text(delta(ir, 1).model_dump_json())]))
    done = await restored.read(ir.document_id)
    assert done.status == "completed" and done.notes.pages_read == [0, 1, 2, 3]
    redo = await ReaderAgent(sv, FakeLLM([text(delta(ir, 1).model_dump_json())])).reread(ir.document_id, 1)
    assert redo.status == "completed" and redo.notes.pages_read == [0, 1, 2, 3]
    async with sv.db.session() as s:
        rows = list((await s.execute(select(ReaderNoteRow))).scalars())
        assert len(rows) == 7
        for row in rows:
            assert row.payload["schema_version"] == "0.6.1"
        artifact = await s.get(ArtifactRow, "t1_abstract")
        artifact.is_active = False
    pending = await ReaderAgent(sv, FakeLLM([])).read(ir.document_id)
    assert (
        pending.status == "pending" and pending.last_page is None and ir.nodes[2].ref in pending.pending_refs
    )


def test_note_rejection_queue_and_pointer_compaction():
    # Pure merge tests use a minimal IR independent of the async fixture.
    ref = "doc/r1/node"
    from drugpaper.agents.reader.page_pack import PagePack, ReadingUnit

    pack = PagePack(document_id="doc", revision=1, page=0, units=[ReadingUnit(items=[{"node_ref": ref}])])
    notes = ReaderNotes()
    bad = NoteDelta(
        page=0, thesis_updates=[Observation(text="bad", page=0, evidence_nodes=["other/r1/node"])]
    )
    assert merge_delta(notes, bad, pack, set(), []) and not notes.thesis
    q = LookupQuestion(question="为什么优化？")
    assert enqueue([q], [q.model_copy(update={"question": " 为什么优化? "})], []) == [q]
    assert not enqueue([q], [], ["为什么优化?"])
    assert not should_flush([q]) and should_flush([q], end=True)
    for i in range(100):
        notes.thesis.append(Observation(text="旧叙事" * 100, page=i, evidence_nodes=[f"doc/r1/node{i}"]))
        notes.pointers[f"doc/r1/node{i}"] = [i]
    compacted = compact(notes, max_tokens=3000)
    assert compacted.tokens() <= 3000 and compacted.pointers == notes.pointers


@pytest.mark.parametrize(
    "a,b,kind",
    [
        ("CN(C)c1ccccc1", "CNc1ccccc1", "n_dealkylation"),
        ("Cc1ccccc1", "Cc1ccncc1", "ring_replacement"),
        ("c1ccccc1", "Fc1ccccc1", "halogenation"),
        ("c1ccccc1", "Cc1ccccc1", "methylation"),
        ("c1ccccc1", "Oc1ccccc1", "hydroxylation"),
        ("CCCC", "C1CCC1", "cyclization"),
        ("C1CCC1", "CCCC", "ring_opening"),
        ("CC", "CCCC", "chain_length_change"),
        ("C[C@H](O)F", "C[C@@H](O)F", "stereochemistry"),
    ],
)
def test_changes_reconstruct(a, b, kind):
    d = fragment_changes(a, b)
    assert kind in classify(a, b, d)
    assert canonical(replace_regions(d)).inchikey == canonical(b).inchikey


async def test_path_negative_projection_and_esmiles(full_env):
    sv, ir = full_env
    ctx = await load_context(sv.db, ir.document_id)
    p = proposal(ir)
    steps = build_steps(p, ctx)
    assert not validate_path(p, steps, ctx), steps
    result = project(p, steps, ctx)
    assert not validate_projection(result, ctx)
    assert result.delivery["分子数据指标"]["毒性"]["hERG Blocker"] == "2 %"
    assert list(result.delivery["优化前后关键指标对比"]["original_molecule_properties"])[-1] == "来源"
    bad = p.model_copy(deep=True)
    bad.steps[0].declared_change_types = ["ring_replacement"]
    bad.steps[0].change_text = "由苯改为吡啶"
    checks = validate_path(bad, build_steps(bad, ctx), ctx)
    assert {"V-P05", "V-P06"} <= {c.code for c in checks}
    bad = p.model_copy(deep=True)
    bad.steps[0].evidence_node_ids = [ir.nodes[4].ref]
    assert "V-P04" in {c.code for c in validate_path(bad, build_steps(bad, ctx), ctx)}
    raw = "CNc1ccccc1<sep><a>0:R1</a>"
    ctx.compounds[1]["accepted_structure"]["e_smiles_raw"] = raw
    result = project(p, build_steps(p, ctx), ctx)
    assert result.delivery["steps"]["1"]["end"]["smiles"] == raw
    assert not validate_projection(result, ctx)
    result.delivery["steps"]["1"]["end"]["smiles"] = raw.split("<sep>")[0]
    assert "V-J03" in {c.code for c in validate_projection(result, ctx)}


async def test_bounded_feedback_exports_and_http(full_env):
    sv, ir = full_env
    bad = proposal(ir).model_copy(deep=True)
    bad.steps[0].declared_change_types = ["ring_replacement"]
    llm = FakeLLM(
        [
            text(delta(ir, 0).model_dump_json()),
            text(delta(ir, 1).model_dump_json()),
            text(bad.model_dump_json()),
            text(proposal(ir).model_dump_json()),
        ]
    )
    pipeline = FullTextPipeline(sv, llm)
    out = await pipeline.run(ir.document_id, prepare_text=False)
    assert out["status"] == "completed" and out["attempts"] == 2, out
    assert "V-P05" in {c["code"] for c in out["history"][0]["checks"]}
    assert "V-P05" in llm.requests[-1].messages[-1].text()
    exported = await create_export(sv, ir.document_id, result_id=out["result_id"])
    data = await sv.store.get(exported["blob_ref"])
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        names = set(z.namelist())
        assert {
            "result.drug_optimization_v1.json",
            "result.drug_optimization_v1.audit.json",
            "paper.corpus.md",
            "paper.audit.md",
            "document.json",
            "compounds.json",
            "measurements.csv",
            "chunks.jsonl",
            "manifest.json",
            "charts/chart.csv",
            "charts/chart.overlay.png",
        } <= names
        manifest = json.loads(z.read("manifest.json"))
        assert any(
            r["component"] == "full_text" and r["metrics"]["model_calls"] == 4
            for r in manifest["call_metrics"]
        )
        assert any(
            r["component"] == "reader_read" and r["metrics"]["model_calls"] == 2
            for r in manifest["call_metrics"]
        )
        for name, meta in manifest["files"].items():
            assert hashlib.sha256(z.read(name)).hexdigest() == meta["sha256"]
        chunks = [json.loads(line) for line in z.read("chunks.jsonl").decode().splitlines()]
        assert all(c["estimated_tokens"] <= 800 for c in chunks)
        corpus = z.read("paper.corpus.md").decode()
        assert "](" in corpus and ")\n\n图像结构化结果：" in corpus
    app = create_app(sv)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test") as client:
        res = await client.post("/api/v1/documents/doc_full/exports", json={})
        assert res.status_code == 201, res.text
        download = await client.get(res.json()["download_url"])
        assert download.status_code == 200 and download.content.startswith(b"PK")
        assert (await client.get("/api/v1/exports/missing/download")).status_code == 404
        assert (await client.post("/api/v1/documents/doc_full/reader/reread", json={})).status_code == 422
    # Failed steps exhaust three attempts and remain reviewable, never a success tag.
    llm = FakeLLM([text(bad.model_dump_json())] * 3)
    failed = await FullTextPipeline(sv, llm).run(ir.document_id, prepare_text=False)
    assert failed["status"] == "needs_review" and failed["attempts"] == 3


async def test_text_measurements_idempotent_stale(full_env):
    sv, ir = full_env
    n = ir.nodes[3]
    async with sv.db.session() as s:
        artifact = await s.get(ArtifactRow, "t1_results")
        artifact.payload = {
            **artifact.payload,
            "measurements": [
                {
                    "compound_label": "26",
                    "metric_text": "hERG",
                    "value_raw": "2%",
                    "evidence": [
                        {
                            "node_id": n.ref,
                            "role": "primary",
                            "char_span": [n.text_raw.index("Compound 26"), len(n.text_raw)],
                        }
                    ],
                }
            ],
        }
    first = await project_text_measurements(sv.db, ir.document_id)
    assert first["measurements"] == 1
    assert (await project_text_measurements(sv.db, ir.document_id))["measurements"] == 0
    async with sv.db.session() as s:
        artifact = await s.get(ArtifactRow, "t1_results")
        artifact.payload = {**artifact.payload, "source_text_sha256": "wrong"}
    ctx = await load_context(sv.db, ir.document_id)
    assert not any(m.get("produced_by_artifact_id") == "t1_results" for m in ctx.measurements)


def test_silver_legacy_shape_and_missing(tmp_path):
    p = {
        "起始分子信息": {"SMILES": "CCO"},
        "steps": {"1": {"start": "CCO", "end": "CCN"}},
        "研究目标标签": ["活性提升"],
        "分子数据指标": {"药效指标": {"IC50": "2 nM", "flag": "Table1"}},
    }
    result = compare(p, p)
    assert result["lead_match"] and result["final_match"] and result["metric_recall"] == 1
    refs = tmp_path / "refs"
    refs.mkdir()
    (refs / "gemini_json_backup_one.json").write_text(json.dumps(p))
    report = evaluate(refs, tmp_path / "pred", expected_count=1)
    assert not report["complete"] and report["rows"][0]["status"] == "missing_prediction"


async def test_vision_grouping_and_page_evidence(full_env):
    import fitz

    from drugpaper.persistence.models import Asset

    sv, ir = full_env
    pdf = fitz.open()
    for _ in ir.pages:
        pdf.new_page()
    data = pdf.tobytes()
    pdf.close()
    blob = await sv.store.put(data, "application/pdf")
    async with sv.db.session() as s:
        s.add(Asset(id="pdf", document_id=ir.document_id, kind="origin_pdf", blob_ref=blob, sha256=blob))
    ctx = await load_context(sv.db, ir.document_id)
    qs = [
        LookupQuestion(question=q, kind="vision", target_page=1)
        for q in ("图中的曲线是什么？", "图中有几个点？")
    ]
    answer = {
        "results": [
            {
                "question": q.question,
                "status": "found",
                "answer": "有两个点。",
                "evidence": [{"node_id": ir.nodes[5].ref}],
            }
            for q in qs
        ]
    }
    llm = FakeLLM([text(json.dumps(answer, ensure_ascii=False))])
    result = await DocLookup(sv, llm).lookup(qs, ctx)
    assert result.model_calls == 1 and result.tool_calls == 0 and len(llm.requests) == 1
    assert llm.tasks == ["vision_lookup"] and all(r.status == "found" for r in result.results)
    assert not llm.requests[0].tools
    # Identical wording on another page cannot reuse this page's cached visual answer.
    qs = [qs[0].model_copy(update={"target_page": 0})]
    llm = FakeLLM(
        [
            text(
                json.dumps(
                    {
                        "results": [
                            {
                                "question": qs[0].question,
                                "status": "found",
                                "answer": "猜測",
                                "evidence": [{"node_id": ir.nodes[5].ref}],
                            }
                        ]
                    },
                    ensure_ascii=False,
                )
            )
        ]
    )
    result = await DocLookup(sv, llm).lookup(qs, ctx)
    assert result.cache_hits == 0 and result.results[0].status == "not_found"


async def test_natural_answer_avoids_lookup(full_env):
    sv, ir = full_env
    d0 = delta(ir, 0)
    d0.open_questions = [LookupQuestion(question="是否进行去甲基化？")]
    d1 = delta(ir, 1)
    d1.resolved_questions = ["是否进行去甲基化？"]
    # Keep the same chapter to allow the later page to answer naturally.
    from drugpaper.persistence.models import NodeRow

    async with sv.db.session() as s:
        n = await s.get(NodeRow, (ir.document_id, 1, "abstract"))
        n.section_role = "results"
    llm = FakeLLM([text(d0.model_dump_json()), text(d1.model_dump_json())])
    result = await ReaderAgent(sv, llm).read(ir.document_id)
    assert result.status == "completed" and not result.notes.open_questions
    assert llm.tasks == ["reader_page", "reader_page"]


async def test_path_chain_tamper_exclusions_opsin_and_continuity(full_env):
    sv, ir = full_env
    ctx = await load_context(sv.db, ir.document_id)
    p = proposal(ir)
    tampered = build_steps(p, ctx)
    tampered[0].diff.regions[0].original_fragment = "CCC"
    assert "V-P02" in {c.code for c in validate_path(p, tampered, ctx)}
    excluded = p.model_copy(update={"excluded_analogs": [{"compound_id": "c26"}]})
    assert "V-P04" in {c.code for c in validate_path(excluded, build_steps(excluded, ctx), ctx)}
    chain = p.model_copy(update={"main_route": ["c26", "c1"]})
    assert "V-P01" in {c.code for c in validate_path(chain, build_steps(chain, ctx), ctx)}
    ctx.structures.append({"compound_id": "c26", "source": "opsin", "inchikey": "different"})
    assert "V-P08" in {c.code for c in validate_path(p, build_steps(p, ctx), ctx)}
    ctx.ir.nodes[3].text_raw = "Compound 26 was converted to compound 1."
    ctx.extractions[ir.nodes[3].ref]["optimization_statements"] = [
        {"from_label": "26", "to_label": "1", "evidence": [{"node_id": ir.nodes[3].ref}]}
    ]
    assert "V-P07" in {c.code for c in validate_path(p, build_steps(p, ctx), ctx)}


def test_multi_region_split_has_sanitized_virtual_intermediate():
    d = fragment_changes("Cc1ccc(Cl)cc1", "CCc1ccc(F)cc1")
    assert len(d.regions) == 2
    virtual = virtual_intermediates(d)
    assert len(virtual) == 1 and canonical(virtual[0]).valid
    assert canonical(virtual[0]).inchikey not in {canonical(d.start).inchikey, canonical(d.end).inchikey}


async def test_projection_conflict_conditions_species_and_validation(full_env):
    sv, ir = full_env
    ctx = await load_context(sv.db, ir.document_id)
    p = proposal(ir)
    m = copy.deepcopy(
        next(m for m in ctx.measurements if m["compound_id"] == "c26" and m["metric"] == "herg")
    )
    m["measurement_id"], m["value_raw"] = "conflict", "99"
    ctx.measurements.append(m)
    result = project(p, build_steps(p, ctx), ctx)
    assert result.delivery["分子数据指标"]["毒性"]["hERG Blocker"] == "null"
    assert any("ambiguous primary metric" in w for w in result.warnings)
    result.delivery["基本信息"]["论文研究内容以及主要贡献"] = "English prose only"
    result.delivery["优化前后关键指标对比"]["original_molecule_properties"]["ic50"] = "Compound 1: 20 nM"
    result.delivery["steps"]["1"]["start"] = "CC"
    codes = {c.code for c in validate_projection(result, ctx)}
    assert {"V-J02", "V-J03", "V-J04", "V-J05", "V-E01"} <= codes
    del result.delivery["基本信息"]
    assert "V-J01" in {c.code for c in validate_projection(result, ctx)}


async def test_sqlite_upgrade_downgrade(tmp_path):
    from drugpaper.persistence.db import Database
    from drugpaper.persistence.migrate import downgrade_async, upgrade_async

    url = f"sqlite+aiosqlite:///{tmp_path}/migration.db"
    await upgrade_async(url)
    db = Database(url)
    async with db.session() as s:
        from sqlalchemy import text as sql_text

        names = set(
            (await s.execute(sql_text("SELECT name FROM sqlite_master WHERE type='table'"))).scalars()
        )
        assert {"lookups", "reader_notes", "full_text_results", "exports"} <= names
    await db.dispose()
    await downgrade_async(url, "0007_figure_agents")
    db = Database(url)
    async with db.session() as s:
        names = set(
            (await s.execute(sql_text("SELECT name FROM sqlite_master WHERE type='table'"))).scalars()
        )
        assert not {"lookups", "reader_notes", "full_text_results", "exports"} & names
    await db.dispose()
