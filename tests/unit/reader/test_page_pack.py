from __future__ import annotations

import pytest

from drugpaper.agents.reader.page_pack import build_page_pack, load_page_pack
from drugpaper.domain.geometry import BBox, Geometry, Region
from drugpaper.domain.nodes import DocumentNode, IRDocument, PageInfo
from drugpaper.persistence.models import NodeRow


def ir_fixture(role="results", raw="compound 26 was tested."):
    ir = IRDocument(
        document_id="doc_reader", pages=[PageInfo(page=p, width_pt=100, height_pt=100) for p in range(2)]
    )
    ir.nodes = [
        DocumentNode(
            document_id=ir.document_id,
            revision=1,
            local_id="p000.b000",
            node_type="paragraph",
            text_raw=raw,
            section_role=role,
            geometry=Geometry(regions=[Region(page=p, bbox=BBox(x0=0, y0=0, x1=1, y1=1)) for p in range(2)]),
        ),
        DocumentNode(
            document_id=ir.document_id,
            revision=1,
            local_id="p000.g000",
            node_type="figure",
            label="Figure 1",
            geometry=Geometry(regions=[Region(page=0, bbox=BBox(x0=0, y0=0, x1=1, y1=1))]),
        ),
    ]
    return ir


def report(ir, status="ok"):
    node = ir.nodes[1]
    return {
        node.ref: {
            "node_ref": node.ref,
            "document_id": ir.document_id,
            "mode": "full",
            "status": status,
            "image_type": "structures",
            "unchecked": [{"field": "stereo"}],
            "extraction": {"molecules": [{"reference": "26", "mol_id": 0}]},
        }
    }


def test_missing_failed_and_review_dependencies_stay_explicit():
    ir = ir_fixture()
    ref = ir.nodes[0].ref
    pack = build_page_pack(ir, 0)
    assert not pack.ready and set(pack.pending_refs) == {node.ref for node in ir.nodes}
    failed = build_page_pack(ir, 0, reports=report(ir, "failed"), extractions={ref: {"status": "completed"}})
    assert not failed.ready and failed.pending_refs == [ir.nodes[1].ref]
    review = build_page_pack(
        ir, 0, reports=report(ir, "needs_review"), extractions={ref: {"status": "needs_review"}}
    )
    assert review.ready and len(review.warnings) == 2
    figure = next(item for unit in review.units for item in unit.items if item["kind"] == "figure")
    assert figure["status"] == "needs_review" and figure["unchecked"] == [{"field": "stereo"}]


def test_cross_page_paragraph_only_in_start_page_and_modes():
    ir = ir_fixture("references")
    first = build_page_pack(ir, 0, reports=report(ir))
    assert first.mode == "skip" and first.ready
    assert build_page_pack(ir, 1).units == []
    ir.nodes[0].section_role = "experimental"
    assert build_page_pack(ir, 0).mode == "skim"
    with pytest.raises(ValueError, match="page outside"):
        build_page_pack(ir, 9)


def test_page_units_keep_full_text_and_bound_tokens():
    content = "化合物26显示更高的活性。" * 2000
    ir = ir_fixture(raw=content)
    pack = build_page_pack(
        ir, 0, max_tokens=700, reports=report(ir), extractions={ir.nodes[0].ref: {"status": "completed"}}
    )
    assert len(pack.units) > 1 and all(unit.estimated_tokens <= 700 for unit in pack.units)
    assert pack.estimated_tokens == sum(unit.estimated_tokens for unit in pack.units)
    parts = [item for unit in pack.units for item in unit.items if item["kind"] == "text"]
    assert "".join(item["text_raw"] for item in parts) == content
    assert all(content[slice(*item["char_span"])] == item["text_raw"] for item in parts)


def test_cross_document_report_cannot_satisfy_readiness():
    ir = ir_fixture()
    reports = report(ir)
    reports[ir.nodes[1].ref]["document_id"] = "other"
    pack = build_page_pack(ir, 0, reports=reports)
    assert not pack.ready and ir.nodes[1].ref in pack.pending_refs


async def test_loading_page_pack_enforces_owner_and_revision(molecule_env):
    sv, _, _, _ = molecule_env
    async with sv.db.session() as session:
        node = await session.get(NodeRow, ("doc_chem", 1, "p000.b000"))
        node.geometry = {"regions": [{"page": 0, "bbox": {"x0": 0, "y0": 0, "x1": 1, "y1": 1}}]}
        node.text_raw = "compound 26"
        node.section_role = "results"
    pack = await load_page_pack(sv.db, "doc_chem", 0)
    assert not pack.ready and "doc_chem/r1/p000.b000" in pack.pending_refs
    with pytest.raises(PermissionError):
        await load_page_pack(sv.db, "doc_chem", 0, user_id="other")
