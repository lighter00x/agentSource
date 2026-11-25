from __future__ import annotations

from collections.abc import Sequence

from drugpaper.domain.geometry import BBox, intersection, iou, page_to_local


def pad_relative(box: BBox, ratio: float = 0.04) -> BBox:
    if ratio < 0:
        raise ValueError("padding 必须非负")
    return BBox(
        x0=max(0, box.x0 - box.width * ratio),
        y0=max(0, box.y0 - box.height * ratio),
        x1=min(1, box.x1 + box.width * ratio),
        y1=min(1, box.y1 + box.height * ratio),
    )


def union_area(boxes: Sequence[BBox]) -> float:
    xs = sorted({x for b in boxes for x in (b.x0, b.x1)})
    area = 0.0
    for left, right in zip(xs, xs[1:], strict=False):
        spans = sorted((b.y0, b.y1) for b in boxes if b.x0 < right and b.x1 > left)
        total, end = 0.0, float("-inf")
        for low, high in spans:
            total += max(0, high - max(low, end))
            end = max(end, high)
        area += (right - left) * total
    return area


def is_whole_image(boxes: Sequence[BBox], img_area: float = 1) -> bool:
    return img_area > 0 and union_area(boxes) / img_area > 0.95


def nested_mask_regions(boxes: Sequence[BBox], threshold: float = 0.45) -> list[list[BBox]]:
    return [
        [
            page_to_local(
                BBox(x0=max(b.x0, s.x0), y0=max(b.y0, s.y0), x1=min(b.x1, s.x1), y1=min(b.y1, s.y1)), b
            )
            for s in boxes
            if 0 < s.area < b.area and intersection(s, b) / s.area > threshold
        ]
        for b in boxes
    ]


def filter_small(boxes: Sequence[BBox], min_ratio: float = 0.015) -> list[BBox]:
    """短边相对父图的比例；按整图面积阈值会误删大型路线图中的合法结构。"""
    return [b for b in boxes if min(b.width, b.height) >= min_ratio]


def reading_order(boxes: Sequence[BBox]) -> list[int]:
    rows: list[list[int]] = []
    for i in sorted(range(len(boxes)), key=lambda j: boxes[j].center()[1]):
        row = next(
            (
                r
                for r in rows
                if abs(boxes[r[0]].center()[1] - boxes[i].center()[1])
                < max(boxes[r[0]].height, boxes[i].height) / 2
            ),
            None,
        )
        if row is None:
            rows.append([i])
        else:
            row.append(i)
    return [i for row in rows for i in sorted(row, key=lambda j: boxes[j].x0)]


def match_ids(new: Sequence[BBox], old: dict[str, BBox], threshold: float = 0.7) -> dict[int, str]:
    pairs = sorted(
        ((iou(b, previous), i, key) for i, b in enumerate(new) for key, previous in old.items()),
        reverse=True,
    )
    matched: dict[int, str] = {}
    used: set[str] = set()
    for score, i, key in pairs:
        if score >= threshold and i not in matched and key not in used:
            matched[i] = key
            used.add(key)
    return matched
