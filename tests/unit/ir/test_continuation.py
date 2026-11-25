"""S2.4c：续段与行级增强（F5、F6）。"""

from __future__ import annotations

from drugpaper.parsers.mineru_legacy.adapter.builder import build_ir
from tests.unit.ir.conftest import needs_real
from tests.unit.ir.real import real_ir
from tests.unit.ir.synth import LONG, blk, mblock, middle, mline, page


def _pt(ir, node_id: str) -> list[tuple[int, str, tuple[float, ...] | None, bool]]:
    return [(r.page, r.role, r.source_bbox_pt, r.derived) for r in ir.get(node_id).geometry.regions]


@needs_real
def test_p0_paragraph_has_left_and_right_regions() -> None:
    ir = real_ir()
    regions = _pt(ir, "p000.b011")
    assert regions == [
        (0, "primary", (50.0, 501.0, 292.0, 748.0), False),
        (0, "continuation", (314.0, 486.0, 556.0, 630.0), False),
    ]
    assert ir.get("p000.b011").attributes["continuation_blocks"] == ["p000.b012"]
    assert len(ir.get("p000.b011").attributes["lines"]) == 35


@needs_real
def test_empty_block_on_next_page_is_continuation() -> None:
    ir = real_ir()
    regions = _pt(ir, "p000.b013")
    assert regions[1][:2] == (1, "continuation")
    assert ir.get("p000.b013").attributes["continuation_blocks"] == ["p001.b000"]


@needs_real
def test_empty_blocks_never_become_nodes() -> None:
    ir = real_ir()
    ids = {n.local_id for n in ir.nodes}
    for empty in ["p000.b009", "p000.b012", "p001.b000", "p001.b004", "p002.b003", "p002.b004", "p015.b000"]:
        assert empty not in ids
    assert "p000.b009" in ir.get("p000.b007").attributes["continuation_blocks"]  # 摘要的全宽续段


@needs_real
def test_caption_member_absorbing_body_text_is_flagged() -> None:
    cap = real_ir().get("p002.g001.c0")
    assert "caption_absorbed_text" in cap.quality_flags
    assert cap.attributes["continuation_blocks"] == ["p002.b003", "p002.b004"]


@needs_real
def test_without_middle_json_regions_are_derived() -> None:
    ir = real_ir(with_middle=False)
    regions = _pt(ir, "p000.b011")
    assert regions[1][:2] == (0, "continuation") and regions[1][3] is True
    assert "derived_continuation" in ir.get("p000.b011").quality_flags
    assert "lines" not in ir.get("p000.b011").attributes
    assert not ir.quality.middle_json_used


def test_synthetic_cross_column_and_stray_lines() -> None:
    layout = [
        page(
            0,
            blk("text", [50, 100, 290, 700], 0, 0, LONG),
            blk("text", [310, 100, 560, 200], 1, 1, ""),
            blk("text", [310, 300, 560, 700], 2, 2, LONG),
        ),
        page(1, blk("text", [50, 50, 290, 90], 0, 0, ""), blk("text", [50, 100, 290, 700], 1, 1, LONG)),
    ]
    mid = middle(
        [
            mblock([50, 100, 290, 700], [mline([50, 100, 290, 110], "a"), mline([310, 110, 560, 120], "b")]),
            mblock([310, 100, 560, 200], deleted=True),
            mblock(
                [310, 300, 560, 700],
                [mline([310, 300, 560, 310], "c"), mline([50, 60, 290, 70], "d", cross_page=True)],
            ),
        ],
        [
            mblock([50, 50, 290, 90], deleted=True),
            mblock([50, 100, 290, 700], [mline([50, 100, 290, 110], "e"), mline([320, 720, 560, 730], "f")]),
        ],
    )
    ir = build_ir(
        layout,
        middle=__import__("drugpaper.parsers.mineru_legacy.schemas", fromlist=["x"]).load_middle(mid),
        document_id="doc_T",
    )
    assert [r[:2] for r in _pt(ir, "p000.b000")] == [(0, "primary"), (0, "continuation")]
    assert [r[:2] for r in _pt(ir, "p000.b002")] == [(0, "primary"), (1, "continuation")]
    # 不在任何块内的行：按栏聚成 region，不标 derived
    stray = _pt(ir, "p001.b001")
    assert stray[1][:2] == (1, "continuation") and stray[1][3] is False
