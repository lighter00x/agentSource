from __future__ import annotations

import json

import httpx2
import pytest
from sqlalchemy import select

from drugpaper.chem.opsin import OpsinClient
from drugpaper.facts.resolver import priority, resolve
from drugpaper.persistence.models import (
    ArtifactRow,
    CompoundRow,
    LabelClaimRow,
    NodeRow,
    StructureCandidateRow,
)
from drugpaper.tools.protocol import ToolContext
from tests.fakes.fake_llm import FakeLLM, text
from tests.unit.chem.test_resolver import candidate

REF = "doc_chem/r1/p000.b000"
RAW = "Ethanol (26). HRMS calcd for C2H7O [M+H]+ 47.0."


async def prepare(molecule_env, *, raw=RAW, status="SUCCESS", smiles="CCO"):
    sv, _, bundle, run = molecule_env
    requests = []

    def handler(request):
        requests.append(request)
        return httpx2.Response(200, json={"status": status, "smiles": smiles})

    sv._opsin = OpsinClient(
        "http://mock/opsin", http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(handler))
    )
    async with sv.db.session() as session:
        node = await session.get(NodeRow, ("doc_chem", 1, "p000.b000"))
        node.text_raw, node.section_role = raw, "experimental"
    return sv, bundle, run, requests


async def test_name_candidate_atomic_projection_cached_and_resolved(molecule_env):
    sv, bundle, run, requests = await prepare(molecule_env)
    result = await run("name_to_structure", REF)
    payload = result.artifacts[0].payload
    structure = payload["structures"][0]
    assert structure["source"] == "opsin" and structure["formula"] == "C2H6O"
    assert structure["validation"]["formula_match"] is True
    assert structure["validation"]["formula_basis"] == "adduct"
    for evidence in structure["evidence"]:
        assert RAW[slice(*evidence["char_span"])]
    assert (await run("name_to_structure", REF)).cached and len(requests) == 1
    resolved = await run("compound_resolve", "doc_chem")
    assert resolved.artifacts[0].payload["compounds"][0]["accepted_structure_id"] == structure["structure_id"]
    async with sv.db.session() as session:
        structures = list((await session.execute(select(StructureCandidateRow))).scalars())
        claims = list((await session.execute(select(LabelClaimRow))).scalars())
        compound = (await session.execute(select(CompoundRow))).scalar_one()
        assert len(structures) == len(claims) == 1
        assert structures[0].artifact_id == claims[0].artifact_id == result.artifacts[0].artifact_id
        assert structures[0].compound_id == compound.id
        assert structures[0].payload["e_smiles_raw"] == "CCO"


@pytest.mark.parametrize(
    "raw,status,smiles,warning",
    [
        ("Ethanol (26). HRMS calcd for C2H4O.", "SUCCESS", "CCO", "V-C03"),
        ("Ethanol (26).", "SUCCESS", "CCO", "HRMS_NOT_VERIFIED"),
        (RAW, "WARNING", "CCO", "OPSIN_WARNING"),
        (RAW, "SUCCESS", "invalid", "OPSIN_INVALID_STRUCTURE"),
    ],
)
async def test_unverified_names_are_not_certified_and_can_retry(molecule_env, raw, status, smiles, warning):
    sv, bundle, run, requests = await prepare(molecule_env, raw=raw, status=status, smiles=smiles)
    result = await run("name_to_structure", REF)
    assert result.artifacts[0].status == "needs_review"
    assert any(warning in w for w in result.artifacts[0].payload["warnings"])
    assert not (await run("name_to_structure", REF)).cached and len(requests) == 2
    resolved = await run("compound_resolve", "doc_chem")
    compounds = resolved.artifacts[0].payload["compounds"]
    if status == "WARNING" or smiles == "invalid":
        assert compounds[0]["accepted_structure_id"] is None
    if warning == "V-C03":
        assert any(c["type"] == "formula" for c in resolved.artifacts[0].payload["conflicts"])


async def test_outage_retires_previous_candidates_and_recovers(molecule_env):
    sv, bundle, run, _ = await prepare(molecule_env)
    old = await run("name_to_structure", REF)
    await sv._opsin.aclose()
    sv._opsin = OpsinClient()
    outage = await run("name_to_structure", REF, force=True)
    assert outage.artifacts[0].status == "needs_review" and not outage.artifacts[0].payload["structures"]
    async with sv.db.session() as session:
        assert not list(
            (
                await session.execute(
                    select(StructureCandidateRow).where(StructureCandidateRow.is_active.is_(True))
                )
            ).scalars()
        )
        assert not list(
            (await session.execute(select(LabelClaimRow).where(LabelClaimRow.is_active.is_(True)))).scalars()
        )
    resolved = await run("compound_resolve", "doc_chem")
    assert all(c["accepted_structure_id"] is None for c in resolved.artifacts[0].payload["compounds"])
    await prepare(molecule_env)
    recovered = await run("name_to_structure", REF)
    assert not recovered.cached and recovered.artifacts[0].status == "completed"
    assert recovered.artifacts[0].artifact_id != old.artifacts[0].artifact_id


async def test_source_edit_blocks_stale_opsin_and_text_materials(molecule_env):
    sv, bundle, run, _ = await prepare(molecule_env)
    await run("name_to_structure", REF)
    async with sv.db.session() as session:
        node = await session.get(NodeRow, ("doc_chem", 1, "p000.b000"))
        node.text_raw = "Methanol (26)."
    ctx = await bundle.context_for(
        ToolContext(document_id="doc_chem", run_id="p", trace_id="t"),
        "doc_chem",
        include_document_image=False,
    )
    assert not ctx.materials.target["structures"]
    result = await bundle.runtime.run("compound_resolve", "doc_chem", {}, ctx)
    assert result.status == "completed"
    assert all(c["accepted_structure_id"] is None for c in result.artifacts[0].payload["compounds"])


async def test_edit_during_name_parse_rolls_back_entire_result(molecule_env):
    sv, bundle, run, _ = await prepare(molecule_env)
    ctx = await bundle.context_for(ToolContext(document_id="doc_chem", run_id="p", trace_id="t"), REF)

    async def mutate(**kwargs):
        async with sv.db.session() as session:
            node = await session.get(NodeRow, ("doc_chem", 1, "p000.b000"))
            node.text_raw = "edited during OPSIN"
        return {"status": "success", "smiles": "CCO"}

    ctx.tools.impls["chem.name_to_structure"] = mutate
    result = await bundle.runtime.run("name_to_structure", REF, {}, ctx)
    assert result.status == "failed" and "改变" in result.error
    async with sv.db.session() as session:
        assert not list((await session.execute(select(StructureCandidateRow))).scalars())
        assert not list((await session.execute(select(LabelClaimRow))).scalars())
        artifacts = await session.execute(
            select(ArtifactRow).where(ArtifactRow.artifact_type == "name_to_structure")
        )
        assert not list(artifacts.scalars())


async def test_t1_mentions_aliases_provisional_roles_and_ledger_atomicity(molecule_env):
    sv, _, bundle, run = molecule_env
    raw = "Compound 26 [UCM-1306] is a lead."
    async with sv.db.session() as session:
        node = await session.get(NodeRow, ("doc_chem", 1, "p000.b000"))
        node.text_raw, node.section_role = raw, "abstract"
    ctx = await bundle.context_for(ToolContext(document_id="doc_chem", run_id="p", trace_id="t"), REF)
    ctx.model = FakeLLM(
        [
            text(
                json.dumps(
                    {
                        "compound_mentions": [
                            {
                                "label": "26",
                                "aliases": ["UCM-1306"],
                                "role": "lead",
                                "char_span": [0, len(raw)],
                            }
                        ]
                    }
                )
            )
        ]
    )
    extracted = await bundle.runtime.run("text_extract", REF, {}, ctx)
    assert extracted.status == "completed"
    resolved = await run("compound_resolve", "doc_chem")
    comp = resolved.artifacts[0].payload["compounds"][0]
    assert (
        comp["aliases"] == ["UCM-1306"] and comp["role"] == "lead" and comp["role_status"] == "needs_review"
    )
    assert comp["evidence"] and comp["role_evidence"]
    assert resolved.artifacts[0].status == "needs_review"
    ctx = await bundle.context_for(
        ToolContext(document_id="doc_chem", run_id="p", trace_id="t"),
        "doc_chem",
        include_document_image=False,
    )
    async with sv.db.session() as session:
        node = await session.get(NodeRow, ("doc_chem", 1, "p000.b000"))
        node.section_role = "references"
    rejected = await bundle.runtime.run("compound_resolve", "doc_chem", {}, ctx)
    assert rejected.status == "failed"


async def test_cross_user_and_old_revision_name_access_rejected(molecule_env):
    _, _, bundle, _ = molecule_env
    with pytest.raises(PermissionError):
        await bundle.context_for(
            ToolContext(user_id="other", document_id="doc_chem", run_id="p", trace_id="t"), REF
        )
    with pytest.raises(ValueError):
        await bundle.context_for(
            ToolContext(document_id="doc_chem", run_id="p", trace_id="t"), "doc_chem/r2/p000.b000"
        )


def test_opsin_priority_and_alias_conflicts_keep_manual_locks():
    low = candidate("low", "CC", "ocsr", 0.1)
    high = candidate("high", "CC", "ocsr", 0.9)
    opsin = candidate("opsin", "CCO", "opsin")
    opsin["validation"]["formula_match"] = True
    assert (
        priority(opsin)
        > priority(high)
        > priority(candidate("markush", "CC", "markush_assembly"))
        > priority(low)
    )
    opsin["validation"]["formula_match"] = False
    assert priority(opsin) < priority(low)
    opsin["validation"]["opsin_status"] = "warning"
    assert priority(opsin) == -1
    evidence = [{"node_id": REF, "role": "primary", "char_span": [0, 10]}]
    data = resolve(
        "doc_chem",
        claims=[],
        structures=[],
        mentions=[
            {"label": "26", "aliases": ["UCM-1306"], "role": "lead", "evidence": evidence},
            {"label": "27", "aliases": ["UCM-1306"], "evidence": evidence},
        ],
        existing=[{"compound_id": "c", "primary_label": "26", "role": "final", "locked_fields": ["role"]}],
    )
    assert all("UCM-1306" not in c["aliases"] for c in data["compounds"])
    assert next(c for c in data["compounds"] if c["primary_label"] == "26")["role"] == "final"
    assert {c["type"] for c in data["conflicts"]} == {"alias"}


async def test_name_source_edit_during_resolve_rejects_snapshot(molecule_env):
    sv, bundle, run, _ = await prepare(molecule_env)
    await run("name_to_structure", REF)
    ctx = await bundle.context_for(
        ToolContext(document_id="doc_chem", run_id="p", trace_id="t"),
        "doc_chem",
        include_document_image=False,
    )
    async with sv.db.session() as session:
        node = await session.get(NodeRow, ("doc_chem", 1, "p000.b000"))
        node.text_raw = "Methanol (26)."
    rejected = await bundle.runtime.run("compound_resolve", "doc_chem", {}, ctx)
    assert rejected.status == "failed"
    async with sv.db.session() as session:
        assert not list((await session.execute(select(CompoundRow))).scalars())


async def test_pipeline_names_and_ledger_without_model_for_opsin(molecule_env):
    from drugpaper.orchestration.text_pipeline import TextPipeline

    sv, bundle, run, requests = await prepare(molecule_env)
    original = bundle.models
    fake = FakeLLM(lambda req: text("{}"))
    bundle.models = fake
    try:
        result = await TextPipeline(sv).run("doc_chem")
        assert result["status"] == "needs_review" and result["p6_complete"] is False
        assert len(result["name_runs"]) == 1 and len(requests) == 1
        compounds = result["compound_resolution"]["artifacts"][0]["payload"]["compounds"]
        assert compounds[0]["primary_label"] == "26" and compounds[0]["accepted_structure_id"]
        assert len(fake.requests) == 1
        cached = await TextPipeline(sv).run("doc_chem")
        assert cached["runs"][0]["cached"] and cached["name_runs"][0]["cached"] and len(requests) == 1
    finally:
        bundle.models = original


def test_missing_explicit_structure_id_does_not_rebind_stale_claim():
    data = resolve(
        "doc",
        claims=[
            {"label": "1", "structure_id": "old", "node_ref": REF},
            {"label": "2", "structure_id": "new", "node_ref": REF},
        ],
        structures=[{**candidate("new", "CCO"), "node_ref": REF}],
    )
    by_label = {c["primary_label"]: c for c in data["compounds"]}
    assert by_label["1"]["accepted_structure_id"] is None
    assert by_label["2"]["accepted_structure_id"] == "new"


async def test_stale_opsin_is_not_delivered_by_reference_or_compound_tools(molecule_env):
    from drugpaper.gateway.base import ToolCall

    sv, bundle, run, _ = await prepare(molecule_env)
    await run("name_to_structure", REF)
    await run("compound_resolve", "doc_chem")
    ctx = ToolContext(document_id="doc_chem", run_id="p", trace_id="t")
    before = await bundle.tools.invoke(
        ctx, ToolCall(id="a", name="mol.lookup_references", arguments={"references": ["26"]})
    )
    assert "26" in before.data["resolved"]
    async with sv.db.session() as session:
        node = await session.get(NodeRow, ("doc_chem", 1, "p000.b000"))
        node.text_raw = "Methanol (26)."
    after = await bundle.tools.invoke(
        ctx, ToolCall(id="b", name="mol.lookup_references", arguments={"references": ["26"]})
    )
    assert after.data["resolved"] == {} and after.data["errors"]
    listed = await bundle.tools.invoke(ctx, ToolCall(id="c", name="list_compounds", arguments={}))
    assert "accepted_structure" not in listed.data["compounds"][0]


@pytest.mark.parametrize("status,match", [("warning", True), ("success", False), ("success", None)])
def test_unverified_opsin_cannot_be_used_to_fill_other_figure_references(status, match):
    from drugpaper.facts.reference_delivery import resolve_references

    s = {**candidate("s", "CCO", "opsin"), "node_ref": REF, "e_smiles_raw": "CCO"}
    s["validation"].update(opsin_status=status, formula_match=match)
    data = resolve_references(["26"], [{"label": "26", "structure_id": "s"}], [s])
    assert data["resolved"] == {} and data["errors"]


async def test_revision_change_rebuilds_ledger_without_old_evidence(molecule_env):
    from drugpaper.persistence.repos.nodes import load_ir, save_ir

    sv, bundle, run, _ = await prepare(molecule_env)
    await run("name_to_structure", REF)
    first = await run("compound_resolve", "doc_chem")
    compound_id = first.artifacts[0].payload["compounds"][0]["compound_id"]
    async with sv.db.session() as session:
        ir = await load_ir(session, "doc_chem", 1)
        ir = ir.model_copy(
            update={"revision": 2, "nodes": [n.model_copy(update={"revision": 2}) for n in ir.nodes]}
        )
        await save_ir(session, ir)
    second_name = await run("name_to_structure", "doc_chem/r2/p000.b000")
    second = await run("compound_resolve", "doc_chem")
    compound = second.artifacts[0].payload["compounds"][0]
    assert compound["compound_id"] == compound_id
    assert (
        compound["accepted_structure_id"] == second_name.artifacts[0].payload["structures"][0]["structure_id"]
    )
    assert compound["evidence"] and all("/r2/" in e["node_id"] for e in compound["evidence"])
