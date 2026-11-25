from __future__ import annotations

import io

import pytest
from PIL import Image

from drugpaper.domain.geometry import BBox, local_to_page, page_to_local
from drugpaper.facts.label_geometry import candidates
from drugpaper.vision.boxes import (
    filter_small,
    is_whole_image,
    match_ids,
    nested_mask_regions,
    pad_relative,
    reading_order,
    union_area,
)
from drugpaper.vision.som import crop_masked, render


def box(x0, y0, x1, y1):
    return BBox(x0=x0, y0=y0, x1=x1, y1=y1)


def test_relative_padding_bounds_and_zero_area_union():
    padded = pad_relative(box(0.9, 0.9, 1, 1))
    assert padded.as_tuple() == pytest.approx((0.896, 0.896, 1, 1))
    assert union_area([]) == 0
    with pytest.raises(ValueError):
        pad_relative(box(0, 0, 1, 1), -1)


def test_union_not_sum_or_enclosing_box():
    assert not is_whole_image([box(0, 0, 0.6, 0.6), box(0.4, 0.4, 1, 1)])
    assert not is_whole_image([box(0, 0, 0.6, 1)] * 2)
    assert is_whole_image([box(0, 0, 0.6, 1), box(0.4, 0, 1, 1)])


def test_nested_masks_small_filter_reading_and_ids():
    small, big = box(0.2, 0.2, 0.3, 0.3), box(0.1, 0.1, 0.9, 0.9)
    masks = nested_mask_regions([big, small])
    assert masks[0][0].as_tuple() == pytest.approx((0.125, 0.125, 0.25, 0.25))
    assert masks[1] == [] and filter_small([box(0.2, 0.2, 0.201, 0.201), small, big]) == [small, big]
    boxes = [box(0.6, 0.1, 0.9, 0.3), box(0.1, 0.11, 0.4, 0.3), box(0.1, 0.6, 0.4, 0.8)]
    assert reading_order(boxes) == [1, 0, 2]
    assert match_ids(list(reversed(boxes)), {f"m{i}": b for i, b in enumerate(boxes)}) == {
        0: "m2",
        1: "m1",
        2: "m0",
    }
    assert len(match_ids([boxes[0]] * 2, {"m0": boxes[0]})) == 1


def test_page_local_coordinate_roundtrip():
    parent = box(0.05, 0.1, 0.8, 0.9)
    child = box(0.12, 0.35, 0.55, 0.75)
    assert page_to_local(local_to_page(child, parent), parent).as_tuple() == pytest.approx(
        child.as_tuple(), abs=0.00001
    )


def test_masking_and_annotations_preserve_geometry():
    image = Image.new("RGB", (200, 100), "black")
    output = io.BytesIO()
    image.save(output, "PNG")
    data = crop_masked(output.getvalue(), box(0, 0, 1, 1), [box(0.2, 0.2, 0.4, 0.4)])
    masked = Image.open(io.BytesIO(data))
    assert masked.getpixel((50, 30)) == (255, 255, 255)
    assert masked.getpixel((100, 50)) == (0, 0, 0)
    marked = Image.open(
        io.BytesIO(
            render(
                output.getvalue(), [{"local_bbox": box(0.1, 0.3, 0.5, 0.9).model_dump(), "reading_index": 0}]
            )
        )
    )
    assert marked.size == image.size and marked.getpixel((20, 40)) == (255, 0, 0)


def test_geometric_labels_page_and_format():
    spans = [
        {"text": "**(S)‑26**", "page": 1, "bbox": box(0.3, 0.55, 0.35, 0.6).model_dump()},
        {"text": "26", "page": 2, "bbox": box(0.3, 0.55, 0.35, 0.6).model_dump()},
        {"text": "reaction", "page": 1, "bbox": box(0.3, 0.55, 0.35, 0.6).model_dump()},
    ]
    assert [c["label"] for c in candidates(box(0.2, 0.2, 0.5, 0.5), spans, 1)] == ["(S)-26"]
