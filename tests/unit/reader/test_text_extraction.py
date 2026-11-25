from __future__ import annotations

import json

import pytest

from drugpaper.capabilities.tokens import estimate_tokens
from drugpaper.facts.text_extraction import rule_mentions, text_chunks, validate_extraction
from drugpaper.facts.text_models import AssayText, Mention, Statement, TextExtraction, TextMeasurement
from drugpaper.persistence.models import ArtifactRow, NodeRow
from drugpaper.skills.registry import BUILTIN_ROOT
from drugpaper.text.normalize import normalize_detail
from drugpaper.tools.protocol import ToolContext
from tests.fakes.fake_llm import FakeLLM, text

REF = "doc_chem/r1/p000.b000"


def test_v_e02_rejects_wrong_label_value_alias_and_bounds():
    raw = r"Compound $26$ [UCM-1306] had IC50 5 nM in human cells."
    detail = normalize_detail(raw)
    span = (0, len(detail.norm))
    result = validate_extraction(
        TextExtraction(
            compound_mentions=[
                Mention(label="26", aliases=["UCM-1306"], char_span=span),
                Mention(label="2", char_span=span),
                Mention(label="26", aliases=["UCM-999"], char_span=span),
                Mention(label="26", char_span=(-1, 4)),
            ],
            measurements=[
                TextMeasurement(
                    compound_label="26",
                    metric_text="IC50",
                    value_raw="5",
                    unit="nM",
                    conditions="human cells",
                    char_span=span,
                ),
                TextMeasurement(compound_label="26", metric_text="IC50", value_raw="50", char_span=span),
            ],
            optimization_statements=[Statement(text="had IC50 5 nM", from_label="99", char_span=span)],
            assay_descriptions=[AssayText(text="in human cells", char_span=span)],
        ),
        detail,
        REF,
    )
    assert result.discarded_count == 5 and result.candidate_count == 8
    assert result.status == "needs_review"
    assert len(result.measurements) == len(result.compound_mentions) == len(result.assay_descriptions) == 1
    evidence = result.measurements[0].evidence[0]
    assert evidence.node_id == REF and "26" in raw[slice(*evidence.char_span)]


def test_rules_expand_ranges_preserve_raw_offsets_and_alias():
    detail = normalize_detail(r"compounds $16-27$ and compound 26 [UCM-1306] were tested.")
    found = rule_mentions(detail, REF)
    assert {str(i) for i in range(16, 28)} <= {item.label for item in found}
    assert any(item.label == "26" and item.aliases == ["UCM-1306"] for item in found)
    assert all("compound" in detail.raw[slice(*item.evidence[0].char_span)] for item in found)
    assert not rule_mentions(normalize_detail("compounds 27-16 and compounds 1-9999"), REF)


def test_chunking_is_bounded_and_lossless():
    content = "化合物26。 " * 2000 + "compound 26 has IC50 5 nM. " * 2000
    chunks = text_chunks(content)
    assert len(chunks) > 1
    assert "".join(chunk for _, chunk in chunks) == content
    assert all(estimate_tokens(chunk) <= 2800 for _, chunk in chunks)
    assert all(content[offset : offset + len(chunk)] == chunk for offset, chunk in chunks)


@pytest.fixture
async def text_env(molecule_env):
    sv, _, bundle, _ = molecule_env
    async with sv.db.session() as session:
        row = await session.get(NodeRow, ("doc_chem", 1, "p000.b000"))
        row.text_raw = r"Compound $26$ [UCM-1306] had IC50 5 nM."
        row.section_role = "abstract"
    assert (await bundle.registry.resolve("text_extract")).path
    yield sv, bundle


async def test_public_text_skill_persistence_cache_and_raw_evidence(text_env):
    sv, bundle = text_env
    original = bundle.models
    try:
        detail = normalize_detail(r"Compound $26$ [UCM-1306] had IC50 5 nM.")
        payload = TextExtraction(
            measurements=[
                TextMeasurement(
                    compound_label="26",
                    metric_text="IC50",
                    value_raw="5",
                    unit="nM",
                    char_span=(0, len(detail.norm)),
                )
            ]
        )
        fake = FakeLLM([text(payload.model_dump_json())])
        bundle.models = fake
        tool = bundle.tools.get("text_extract")
        ctx = ToolContext(document_id="doc_chem", run_id="parent", trace_id="trace")
        result = await tool.run(ctx, tool.spec.input_model.model_validate({"target_ref": REF}))
        assert result.ok and not result.data["needs_review"]
        art = result.data["artifacts"][0]
        assert art["payload"]["measurements"][0]["evidence"][0]["node_id"] == REF
        again = await tool.run(ctx, tool.spec.input_model.model_validate({"target_ref": REF}))
        assert again.data["cached"] and len(fake.requests) == 1
        assert fake.tasks == ["text_extract"] and not fake.requests[0].tools
        async with sv.db.session() as session:
            assert (await session.get(ArtifactRow, art["artifact_id"])).payload == art["payload"]
        async with sv.db.session() as session:
            row = await session.get(NodeRow, ("doc_chem", 1, "p000.b000"))
            row.section_role = "introduction"
        rules = await tool.run(ctx, tool.spec.input_model.model_validate({"target_ref": REF}))
        assert rules.ok and not rules.data["cached"] and len(fake.requests) == 1
    finally:
        bundle.models = original


async def test_text_skill_rejects_changed_text_before_commit(text_env):
    sv, bundle = text_env
    ctx = await bundle.context_for(
        ToolContext(document_id="doc_chem", run_id="parent", trace_id="trace"), REF
    )
    ctx.model = FakeLLM([text("{}")])
    async with sv.db.session() as session:
        row = await session.get(NodeRow, ("doc_chem", 1, "p000.b000"))
        row.text_raw = "changed compound 99"
    result = await bundle.runtime.run("text_extract", REF, {}, ctx)
    assert result.status == "failed" and "改变" in result.error


async def test_text_skill_records_discarded_items_as_review(text_env):
    _, bundle = text_env
    ctx = await bundle.context_for(
        ToolContext(document_id="doc_chem", run_id="parent", trace_id="trace"), REF
    )
    ctx.model = FakeLLM([text(json.dumps({"compound_mentions": [{"label": "99", "char_span": [0, 10]}]}))])
    result = await bundle.runtime.run("text_extract", REF, {}, ctx)
    assert result.artifacts[0].status == "needs_review"
    assert result.artifacts[0].payload["discarded_count"] == 1


async def test_text_package_registered_replay(text_env):
    _, bundle = text_env
    skill = await bundle.registry.resolve("text_extract")
    assert skill.content_hash
    assert (BUILTIN_ROOT / "text_extract" / "schemas/output.schema.json").exists()


async def test_long_node_chunks_offset_and_budget(text_env):
    sv, bundle = text_env
    raw = "Background sentence. " * 1200 + "compound 26 [UCM-1306] had IC50 5 nM."
    async with sv.db.session() as session:
        node = await session.get(NodeRow, ("doc_chem", 1, "p000.b000"))
        node.text_raw = raw
    ctx = await bundle.context_for(
        ToolContext(document_id="doc_chem", run_id="parent", trace_id="trace"), REF
    )

    def respond(req):
        content = json.loads(req.messages[1].text())["text_norm"]
        facts = []
        if "IC50" in content:
            start = content.index("compound 26")
            facts = [
                {
                    "compound_label": "26",
                    "metric_text": "IC50",
                    "value_raw": "5",
                    "unit": "nM",
                    "char_span": [start, len(content)],
                }
            ]
        return text(json.dumps({"measurements": facts}))

    fake = FakeLLM(respond)
    ctx.model = fake
    result = await bundle.runtime.run("text_extract", REF, {}, ctx)
    assert result.status == "completed" and len(fake.requests) > 1
    found = result.artifacts[0].payload["measurements"]
    assert len(found) == 1
    assert "IC50 5 nM" in raw[slice(*found[0]["evidence"][0]["char_span"])]
    assert all(
        estimate_tokens(json.loads(req.messages[1].text())["text_norm"]) <= 2800 for req in fake.requests
    )


async def test_experimental_name_has_raw_evidence(text_env):
    sv, bundle = text_env
    raw = "2-Fluorophenol (26). HRMS calcd for C6H5FO."
    async with sv.db.session() as session:
        node = await session.get(NodeRow, ("doc_chem", 1, "p000.b000"))
        node.text_raw = raw
        node.section_role = "experimental"
    ctx = await bundle.context_for(
        ToolContext(document_id="doc_chem", run_id="parent", trace_id="trace"), REF
    )
    ctx.model = FakeLLM([text("{}")])
    result = await bundle.runtime.run("text_extract", REF, {}, ctx)
    name = result.artifacts[0].payload["experimental_names"][0]
    assert name["name"] == "2-Fluorophenol" and name["label"] == "26"
    assert raw[slice(*name["evidence"][0]["char_span"])] == "2-Fluorophenol (26)."


async def test_pipeline_preserves_missing_figure_dependency(text_env):
    from drugpaper.orchestration.text_pipeline import TextPipeline

    sv, bundle = text_env
    original = bundle.models
    try:
        bundle.models = FakeLLM(lambda req: text("{}"))
        result = await TextPipeline(sv).run("doc_chem")
        assert result["status"] == "needs_review" and len(result["runs"]) == 1
        assert result["runs"][0]["status"] == "completed"
        assert not result["page_packs"][0]["ready"]
    finally:
        bundle.models = original


def test_unique_quote_can_repair_offsets_but_never_wrong_claims():
    detail = normalize_detail("Compound 26 has IC50 5 nM. More details follow.")
    result = validate_extraction(
        TextExtraction(
            measurements=[
                TextMeasurement(
                    compound_label="26",
                    metric_text="IC50",
                    value_raw="5",
                    char_span=(0, 1),
                    evidence_text="Compound 26 has IC50 5 nM.",
                ),
                TextMeasurement(
                    compound_label="26",
                    metric_text="IC50",
                    value_raw="50",
                    char_span=(0, 1),
                    evidence_text="Compound 26 has IC50 5 nM.",
                ),
            ]
        ),
        detail,
        REF,
    )
    assert len(result.measurements) == 1 and result.discarded_count == 1 and result.status == "needs_review"
    assert detail.norm[slice(*result.measurements[0].char_span)] == "Compound 26 has IC50 5 nM."
    repeated = normalize_detail("Compound 26. Compound 26.")
    rejected = validate_extraction(
        TextExtraction(
            compound_mentions=[Mention(label="26", char_span=(0, 1), evidence_text="Compound 26.")]
        ),
        repeated,
        REF,
    )
    assert not rejected.compound_mentions and rejected.discarded_count == 1


async def test_page_pack_rejects_extraction_after_source_edit(text_env):
    from drugpaper.agents.reader.page_pack import load_page_pack

    sv, bundle = text_env
    ctx = await bundle.context_for(
        ToolContext(document_id="doc_chem", run_id="parent", trace_id="trace"), REF
    )
    ctx.model = FakeLLM([text("{}")])
    result = await bundle.runtime.run("text_extract", REF, {}, ctx)
    assert result.status == "completed" and result.artifacts[0].payload["source_text_sha256"]
    async with sv.db.session() as session:
        node = await session.get(NodeRow, ("doc_chem", 1, "p000.b000"))
        node.text_raw = "Compound 99 has different data."
        node.geometry = {"regions": [{"page": 0, "bbox": {"x0": 0, "y0": 0, "x1": 1, "y1": 1}}]}
    pack = await load_page_pack(sv.db, "doc_chem", 0)
    assert REF in pack.pending_refs
    assert not any(item.get("kind") == "compound_mentions" for unit in pack.units for item in unit.items)
