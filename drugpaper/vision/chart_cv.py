from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

import cv2
import numpy as np
from PIL import Image, ImageDraw

from drugpaper.facts.figure_models import Tick


@dataclass
class Calibration:
    slope: float
    intercept: float
    scale: str
    residual: float
    rejected: list[int]

    def value(self, pixel: float) -> float:
        result = self.slope * pixel + self.intercept
        return 10**result if self.scale == "log10" else result

    def pixel(self, value: float) -> float:
        if self.scale == "log10":
            value = math.log10(value)
        return (value - self.intercept) / self.slope


def calibrate(ticks: list[Tick], scale: str = "linear") -> Calibration:
    if len(ticks) < 2 or len({t.pixel for t in ticks}) < 2:
        raise ValueError("C_AXIS_RESIDUAL: fewer than two distinct ticks")
    pixels = np.array([t.pixel for t in ticks], dtype=float)
    values = np.array([t.value for t in ticks], dtype=float)
    if scale == "log10":
        if np.any(values <= 0):
            raise ValueError("C_AXIS_SCALE: log ticks must be positive")
        values = np.log10(values)
    # Pair consensus avoids a single outlier distorting all residuals.
    best = np.arange(len(ticks))
    threshold = 0.02 * max(float(np.ptp(pixels)), 1)
    candidates = []
    for i in range(len(ticks)):
        for j in range(i + 1, len(ticks)):
            if pixels[i] == pixels[j] or values[i] == values[j]:
                continue
            a = (values[j] - values[i]) / (pixels[j] - pixels[i])
            b = values[i] - a * pixels[i]
            residual = np.abs((values - b) / a - pixels)
            mask = np.where(residual <= threshold)[0]
            candidates.append((len(mask), float(residual[mask].sum()), mask))
    if candidates:
        best = min(candidates, key=lambda c: (-c[0], c[1]))[2]
    if len(best) < 2:
        raise ValueError("C_AXIS_RESIDUAL: calibration failed")
    a, b = np.polyfit(pixels[best], values[best], 1)
    if abs(a) < 1e-15:
        raise ValueError("C_AXIS_SCALE: constant axis")
    residual = float(np.max(np.abs((values[best] - b) / a - pixels[best])) / max(float(np.ptp(pixels)), 1))
    return Calibration(float(a), float(b), scale, residual, [i for i in range(len(ticks)) if i not in best])


def extract_series(
    image: Image.Image, colors: dict[str, list[int]], plot_area: tuple[int, int, int, int], chart_type: str
) -> tuple[dict[str, list[list[float]]], dict[str, Any]]:
    rgb = np.asarray(image.convert("RGB"))
    lab = cv2.cvtColor(rgb, cv2.COLOR_RGB2LAB).astype(float)
    x0, y0, x1, y1 = plot_area
    out = {}
    masks = {}
    for name, color in colors.items():
        target = cv2.cvtColor(np.array([[color]], dtype=np.uint8), cv2.COLOR_RGB2LAB)[0, 0].astype(float)
        mask = (np.linalg.norm(lab - target, axis=2) < 24).astype(np.uint8)
        region = np.zeros(mask.shape, dtype=np.uint8)
        region[y0:y1, x0:x1] = mask[y0:y1, x0:x1]
        masks[name] = region
        points = []
        if chart_type in {"scatter", "bar"}:
            count, labels, stats, centers = cv2.connectedComponentsWithStats(region, connectivity=8)
            for i in range(1, count):
                x, y, w, h, area = stats[i]
                if area < 5:
                    continue
                points.append(
                    [float(centers[i][0]), float(y) if chart_type == "bar" else float(centers[i][1])]
                )
        else:
            for x in range(x0, x1):
                ys = np.flatnonzero(region[:, x])
                if len(ys):
                    points.append([float(x), float(np.median(ys))])
        out[name] = sorted(points)
    return out, masks


def overlay(image: Image.Image, points: dict[str, list[list[float]]]) -> Image.Image:
    out = image.convert("RGB").copy()
    draw = ImageDraw.Draw(out)
    for series in points.values():
        for x, y in series:
            draw.ellipse((x - 2, y - 2, x + 2, y + 2), outline="magenta", width=2)
    return out


def overlap_iou(
    shape: tuple[int, int], points: list[list[float]], mask: Any, chart_type: str = "line"
) -> float:
    rendered = np.zeros(shape, dtype=np.uint8)
    xy = np.asarray(points, dtype=np.int32)
    if len(xy) == 0:
        return 0.0
    if chart_type == "line" and len(xy) > 1:
        cv2.polylines(rendered, [xy], False, 1, 3)
    else:
        for x, y in xy:
            cv2.circle(rendered, (int(x), int(y)), 3, 1, -1)
    source = mask > 0
    target = rendered > 0
    union = np.logical_or(source, target).sum()
    return float(np.logical_and(source, target).sum() / union) if union else 0.0
