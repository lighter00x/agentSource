"""节点快照与质检。快照更新：`UPDATE_IR_SNAPSHOT=1 uv run pytest tests/unit/ir/test_builder_snapshot.py`，更新后需人工核对再提交。"""

from __future__ import annotations

import json
import os

from drugpaper.parsers.mineru_legacy.adapter.builder import build_ir
from drugpaper.parsers.mineru_legacy.snapshot import ir_snapshot
from tests.unit.ir.conftest import SNAPSHOT_PATH, needs_real
from tests.unit.ir.real import real_ir
from tests.unit.ir.synth import LONG, blk, page


@needs_real
def test_snapshot_matches() -> None:
    snap = ir_snapshot(real_ir())
    if os.environ.get("UPDATE_IR_SNAPSHOT") == "1" or not SNAPSHOT_PATH.exists():
        SNAPSHOT_PATH.write_text(json.dumps(snap, ensure_ascii=False, indent=1), encoding="utf-8")
    expected = json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8"))
    assert snap["type_counts"] == expected["type_counts"]
    assert snap["meta"] == expected["meta"]
    assert snap["quality"] == expected["quality"]
    assert snap["nodes"] == expected["nodes"]


@needs_real
def test_quality_report_lists_f3_f4() -> None:
    counts = real_ir().quality.counts()
    assert counts["caption_mismatch"] >= 2 and counts["absorbed_by_merge"] == 1
    assert "overlap" not in counts and "bbox_out_of_bounds" not in counts
    assert real_ir().quality.middle_json_used


def test_pdf_page_size_overrides_layout_page_size() -> None:
    ir = build_ir(
        [page(0, blk("text", [60, 80, 300, 400], 0, 0, LONG))],
        document_id="doc_T",
        pdf_page_sizes=[(600.0, 800.0)],
    )
    b = ir.get("p000.b000").geometry.regions[0].bbox
    assert (b.x0, b.y1) == (0.1, 0.5)
    assert ir.pages[0].width_pt == 600.0
    assert ir.quality.counts()["page_size_mismatch"] == 1


def test_contained_visual_demoted_and_checks() -> None:
    ir = build_ir(
        [
            page(
                0,
                blk("image_body", [50, 50, 550, 500], 0, 0, caption_text="Figure 1. Big"),
                blk("image_body", [100, 100, 200, 200], 1, 1),
                blk("text", [-20, 600, 700, 700], 2, 2, LONG),
                blk("image_body", [50, 50, 550, 500], 3, 3, img_path="images/missing.jpg"),
            )
        ],
        document_id="doc_T",
        pdf_page_count=2,
        scanned_pages=[0],
        available_assets=set(),
    )
    small = ir.get("p000.b001")
    assert small.node_type == "figure_part" and small.parent_id == "p000.b000"
    counts = ir.quality.counts()
    assert counts["bbox_out_of_bounds"] == 1
    assert counts["page_count_mismatch"] == 1 and counts["scanned_page"] == 1
    assert counts["asset_missing"] == 1
    assert ir.quality.scanned_pages == [0]
