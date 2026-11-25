from __future__ import annotations

import pytest

from drugpaper.parsers.mineru_legacy.schemas import LayoutBlock, load_layout, load_middle
from tests.unit.ir.conftest import LAYOUT_PATH, MIDDLE_PATH, needs_real


@needs_real
def test_real_layout_and_middle_parse() -> None:
    pages = load_layout(LAYOUT_PATH.read_bytes())
    assert len(pages) == 17
    assert sum(len(p.para_blocks) for p in pages) == 148
    assert all(b.page_idx == p.page_idx for p in pages for b in p.para_blocks)
    middle = load_middle(MIDDLE_PATH.read_bytes())
    assert len(middle.pdf_info) == 17
    p0 = middle.page(0)
    assert p0 is not None and len(p0.para_blocks) == 14 and len(p0.discarded_blocks) == 13
    assert p0.para_blocks[12].lines_deleted is True
    assert any(line.cross_page for b in p0.para_blocks for line in b.lines)


def test_unknown_fields_kept_and_bbox_validated() -> None:
    b = LayoutBlock.model_validate({"type": "text", "bbox": [1, 2, 3, 4], "novel_field": {"x": 1}})
    assert b.bbox == (1.0, 2.0, 3.0, 4.0)
    assert b.extra_fields() == {"novel_field": {"x": 1}}
    with pytest.raises(ValueError):
        LayoutBlock.model_validate({"bbox": [1, 2]})


def test_bad_top_level() -> None:
    with pytest.raises(ValueError):
        load_layout(b"{}")
    with pytest.raises(ValueError):
        load_middle(b"[]")
