"""S2.4a：单元识别与类型判定。"""

from __future__ import annotations

import pytest

from drugpaper.parsers.mineru_legacy.adapter.builder import build_ir
from drugpaper.parsers.mineru_legacy.adapter.node_types import classify_visual, find_label, is_footnote_text
from drugpaper.parsers.mineru_legacy.adapter.units import identify_units
from drugpaper.parsers.mineru_legacy.schemas import load_layout
from tests.unit.ir.synth import LONG, blk, page


@pytest.mark.parametrize(
    ("text", "label"),
    [
        ("Table 1. Effect of Compounds", "Table 1"),
        ("Fig. 3 Dose response", "Figure 3"),
        ("Scheme S2. Synthesis", "Scheme S2"),
        ("Figure 4A shows", "Figure 4A"),
        ("Chart IV", "Chart IV"),
        (r"Table\mathrm { 2 }. x", "Table 2"),  # 规范化后匹配
        ("The Table 1 shows", None),
        ("1-10", None),
    ],
)
def test_find_label(text: str, label: str | None) -> None:
    found = find_label(text)
    assert (found.text if found else None) == label


def test_classify_visual() -> None:
    assert classify_visual(find_label("Table 2. x"), "image_body") == ("table", None)
    assert classify_visual(find_label("Scheme 1. x"), "image_body") == ("scheme", None)
    assert classify_visual(None, "image_body", in_abstract_area=True) == ("figure", "toc_graphic")
    assert classify_visual(None, "image_body", parent_type="table") == ("table", None)
    assert classify_visual(None, "table_body") == ("table", None)


def test_footnote_pattern() -> None:
    assert is_footnote_text("aReagents and conditions")
    assert is_footnote_text("a Effect over")
    assert not is_footnote_text("Table 1. x")
    assert not is_footnote_text("and the results")


def test_members_found_by_merged_from_without_belong_to() -> None:
    layout = load_layout(
        [
            page(
                0,
                blk("image_body", [50, 50, 550, 400], None, 0, merged_from=[0, 1]),
                blk("text", [50, 50, 300, 60], 0, None, "Figure 1. A figure"),
                blk("image_body", [50, 65, 550, 400], 1, None),
                blk("text", [50, 410, 550, 700], 2, 1, "Body text."),
            )
        ]
    )
    units = identify_units(layout)
    assert [(u.kind, u.anchor.detailed_index) for u in units] == [("group", None), ("block", 2)]
    assert [m.detailed_index for _, m in units[0].members] == [0, 1]


def test_text_group_is_dissolved() -> None:
    ir = build_ir(
        [
            page(
                0,
                blk("text", [50, 50, 550, 100], None, 0, merged_from=[0, 1]),
                blk("text", [50, 50, 290, 100], 0, None, "INTRODUCTION"),
                blk("text", [300, 50, 550, 100], 1, None, LONG),
            )
        ],
        document_id="doc_T",
    )
    ids = {n.local_id for n in ir.nodes}
    assert "p000.g000" not in ids
    assert {"p000.b000", "p000.b001"} <= ids
    assert ir.get("p000.b001").source_ref["dissolved_group"] == 0


def test_cross_page_group() -> None:
    ir = build_ir(
        [
            page(
                0,
                blk("text", [50, 50, 550, 300], 0, 0, LONG),
                blk(
                    "image_body",
                    [50, 400, 550, 780],
                    1,
                    1,
                    merged_from_cross=[{"page_idx": 1, "detailed_index": 0}],
                    caption_text="Figure 2. Split figure",
                    sub_blocks=[
                        {
                            "type": "image_caption",
                            "bbox": [50, 760, 300, 780],
                            "text": "Figure 2. Split figure",
                        }
                    ],
                ),
            ),
            page(
                1,
                blk("image_body", [50, 40, 550, 300], 0, None, belong_to_cross=[0, 1]),
                blk("text", [50, 320, 550, 700], 1, 0, LONG),
            ),
        ],
        document_id="doc_T",
    )
    x = ir.get("x000.g001")
    assert x.node_type == "figure" and x.label == "Figure 2"
    assert [(r.page, r.role) for r in x.geometry.regions] == [(0, "segment"), (1, "segment")]
    assert "p001.b000" not in {n.local_id for n in ir.nodes if n.node_type != "figure_part"}


def test_unknown_block_type_kept() -> None:
    ir = build_ir([page(0, blk("mystery", [10, 10, 100, 100], 0, 0, "???"))], document_id="doc_T")
    assert ir.get("p000.b000").node_type == "unknown"
    assert ir.quality.counts()["unknown_block_type"] == 1
