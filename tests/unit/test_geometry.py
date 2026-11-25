from __future__ import annotations

import random

import pytest

from drugpaper.domain.geometry import (
    BBox,
    iou,
    local_to_page,
    norm_to_pt,
    norm_to_px,
    overlap_ratio,
    page_to_local,
    pt_out_of_bounds,
    pt_to_norm,
    union_bbox,
)
from drugpaper.domain.ids import block_id, global_ref, group_id, new_document_id, parse_ref, ulid


def test_pt_to_norm_and_back() -> None:
    b = pt_to_norm([61.2, 79.2, 306, 396], [612, 792])
    assert b.as_tuple() == pytest.approx((0.1, 0.1, 0.5, 0.5))
    assert norm_to_pt(b, 612, 792) == pytest.approx((61.2, 79.2, 306, 396))


def test_pt_to_norm_clamps_and_out_of_bounds() -> None:
    b = pt_to_norm([-10, 0, 700, 792], [612, 792])
    assert b.x0 == 0 and b.x1 == 1
    assert pt_out_of_bounds([-10, 0, 700, 792], [612, 792])
    assert not pt_out_of_bounds([0, 0, 612, 792], [612, 792])
    with pytest.raises(ValueError):
        pt_to_norm([0, 0, 1, 1], [0, 792])


def test_norm_to_px_rounds_outward() -> None:
    assert norm_to_px(BBox(x0=0.1, y0=0.1, x1=0.55, y1=0.5), 100, 200) == (10, 20, 55, 100)


def test_local_page_roundtrip() -> None:
    rnd = random.Random(0)
    for _ in range(200):
        px0, py0 = rnd.random() * 0.5, rnd.random() * 0.5
        parent = BBox(x0=px0, y0=py0, x1=px0 + 0.1 + rnd.random() * 0.4, y1=py0 + 0.1 + rnd.random() * 0.4)
        lx0, ly0 = rnd.random() * 0.5, rnd.random() * 0.5
        local = BBox(x0=lx0, y0=ly0, x1=lx0 + rnd.random() * 0.5, y1=ly0 + rnd.random() * 0.5)
        back = page_to_local(local_to_page(local, parent), parent)
        assert max(abs(a - b) for a, b in zip(back.as_tuple(), local.as_tuple(), strict=True)) < 1e-6


def test_iou_overlap_union() -> None:
    a = BBox(x0=0, y0=0, x1=0.5, y1=0.5)
    b = BBox(x0=0.25, y0=0.25, x1=0.75, y1=0.75)
    assert iou(a, b) == pytest.approx(0.0625 / (0.25 + 0.25 - 0.0625))
    assert overlap_ratio(BBox(x0=0.1, y0=0.1, x1=0.2, y1=0.2), a) == 1
    assert union_bbox([a, b]).as_tuple() == (0, 0, 0.75, 0.75)
    assert iou(a, BBox(x0=0.6, y0=0.6, x1=0.7, y1=0.7)) == 0


def test_ids() -> None:
    u1, u2 = ulid(1_000), ulid(2_000)
    assert len(u1) == 26 and u1 < u2
    d = new_document_id()
    assert d.startswith("doc_") and len(d) == 30
    ref = global_ref(d, 1, "p003.g000.c0")
    assert parse_ref(ref) == (d, 1, "p003.g000.c0")
    assert parse_ref("p000.b007") is None
    assert block_id(0, 7) == "p000.b007" and group_id(3, 0) == "p003.g000"
