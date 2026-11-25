"""坐标体系（设计文档 6.3 节）。

- 页面坐标统一归一化到 [0,1]（左上原点），同时保留原始 PDF 点坐标；
- 子节点（分子框、Panel）用 `local_bbox + parent_region_index` 表示，页面坐标由父 region 推导。
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Literal

from pydantic import BaseModel, Field

PtBox = tuple[float, float, float, float]


class BBox(BaseModel):
    x0: float
    y0: float
    x1: float
    y1: float

    @property
    def width(self) -> float:
        return max(0.0, self.x1 - self.x0)

    @property
    def height(self) -> float:
        return max(0.0, self.y1 - self.y0)

    @property
    def area(self) -> float:
        return self.width * self.height

    def as_tuple(self) -> tuple[float, float, float, float]:
        return (self.x0, self.y0, self.x1, self.y1)

    def center(self) -> tuple[float, float]:
        return ((self.x0 + self.x1) / 2, (self.y0 + self.y1) / 2)

    def contains_point(self, x: float, y: float, tol: float = 0.0) -> bool:
        return self.x0 - tol <= x <= self.x1 + tol and self.y0 - tol <= y <= self.y1 + tol

    def in_bounds(self, eps: float = 1e-6) -> bool:
        return (
            all(-eps <= v <= 1 + eps for v in self.as_tuple()) and self.x0 <= self.x1 and self.y0 <= self.y1
        )


class Region(BaseModel):
    page: int
    bbox: BBox  # 页面归一化坐标
    source_bbox_pt: PtBox | None = None  # 原始 PDF 点坐标
    role: Literal["primary", "continuation", "segment"] = "primary"
    derived: bool = False  # 由启发式推导（无行级证据），UI 中可见


class Geometry(BaseModel):
    regions: list[Region] = Field(default_factory=list)  # 段落续段、跨页图可有多个
    local_bbox: BBox | None = None  # 子节点相对父 region 的坐标（权威值）
    parent_region_index: int | None = None  # 子节点属于父节点的哪个 region
    derived: bool = False  # regions 是否由 local_bbox 推导

    @property
    def page(self) -> int | None:
        return self.regions[0].page if self.regions else None

    def pages(self) -> list[int]:
        return sorted({r.page for r in self.regions})


# ---------------------------------------------------------------- 换算


def _clamp(v: float) -> float:
    return min(max(v, 0.0), 1.0)


def pt_to_norm(bbox_pt: Sequence[float], page_size: Sequence[float], *, clamp: bool = True) -> BBox:
    w, h = float(page_size[0]), float(page_size[1])
    if w <= 0 or h <= 0:
        raise ValueError(f"非法页面尺寸：{page_size}")
    x0, y0, x1, y1 = (float(v) for v in bbox_pt)
    vals = (x0 / w, y0 / h, x1 / w, y1 / h)
    if clamp:
        vals = tuple(_clamp(v) for v in vals)  # type: ignore[assignment]
    return BBox(x0=vals[0], y0=vals[1], x1=vals[2], y1=vals[3])


def pt_out_of_bounds(bbox_pt: Sequence[float], page_size: Sequence[float], tol_pt: float = 2.0) -> bool:
    w, h = float(page_size[0]), float(page_size[1])
    x0, y0, x1, y1 = (float(v) for v in bbox_pt)
    return x0 < -tol_pt or y0 < -tol_pt or x1 > w + tol_pt or y1 > h + tol_pt or x0 > x1 or y0 > y1


def norm_to_pt(bbox: BBox, page_w: float, page_h: float) -> PtBox:
    return (bbox.x0 * page_w, bbox.y0 * page_h, bbox.x1 * page_w, bbox.y1 * page_h)


def norm_to_px(bbox: BBox, width_px: int, height_px: int) -> tuple[int, int, int, int]:
    """归一化坐标 → 像素坐标（向外取整，保证不丢边）。"""
    import math

    eps = 1e-9  # 抵消浮点误差（0.55 * 100 = 55.000000000000007）
    return (
        max(0, math.floor(bbox.x0 * width_px + eps)),
        max(0, math.floor(bbox.y0 * height_px + eps)),
        min(width_px, math.ceil(bbox.x1 * width_px - eps)),
        min(height_px, math.ceil(bbox.y1 * height_px - eps)),
    )


def local_to_page(local_bbox: BBox, parent: BBox) -> BBox:
    """子节点局部坐标（相对父 region，[0,1]）→ 页面归一化坐标。"""
    return BBox(
        x0=parent.x0 + local_bbox.x0 * parent.width,
        y0=parent.y0 + local_bbox.y0 * parent.height,
        x1=parent.x0 + local_bbox.x1 * parent.width,
        y1=parent.y0 + local_bbox.y1 * parent.height,
    )


def page_to_local(bbox: BBox, parent: BBox) -> BBox:
    """页面归一化坐标 → 相对父 region 的局部坐标。"""
    if parent.width <= 0 or parent.height <= 0:
        raise ValueError("父 region 面积为 0")
    return BBox(
        x0=(bbox.x0 - parent.x0) / parent.width,
        y0=(bbox.y0 - parent.y0) / parent.height,
        x1=(bbox.x1 - parent.x0) / parent.width,
        y1=(bbox.y1 - parent.y0) / parent.height,
    )


def intersection(a: BBox, b: BBox) -> float:
    w = min(a.x1, b.x1) - max(a.x0, b.x0)
    h = min(a.y1, b.y1) - max(a.y0, b.y0)
    return w * h if w > 0 and h > 0 else 0.0


def iou(a: BBox, b: BBox) -> float:
    inter = intersection(a, b)
    union = a.area + b.area - inter
    return inter / union if union > 0 else 0.0


def overlap_ratio(small: BBox, big: BBox) -> float:
    """small 有多大比例落在 big 内。"""
    return intersection(small, big) / small.area if small.area > 0 else 0.0


def union_bbox(boxes: Sequence[BBox]) -> BBox:
    return BBox(
        x0=min(b.x0 for b in boxes),
        y0=min(b.y0 for b in boxes),
        x1=max(b.x1 for b in boxes),
        y1=max(b.y1 for b in boxes),
    )
