import io

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pytest
from PIL import Image

from drugpaper.facts.figure_models import Tick
from drugpaper.vision.chart_cv import calibrate, extract_series, overlap_iou


@pytest.mark.parametrize("case", range(20))
def test_twenty_synthetic_charts_bars_lines_scatter_log_axes(case):
    kind = ["bar", "line", "scatter"][case % 3]
    log = case % 4 == 0
    x = np.arange(1, 6, dtype=float)
    y = np.random.default_rng(100 + case).uniform(1.5, 6.5, size=5) * (10 if log else 1)
    fig, ax = plt.subplots(figsize=(4, 3), dpi=100)
    if kind == "bar":
        ax.bar(x, y, color="#e02020", width=0.5)
    elif kind == "line":
        ax.plot(x, y, color="#e02020", linewidth=2)
    else:
        ax.scatter(x, y, color="#e02020", s=36)
    ax.set_xlim(0.5, 5.5)
    ax.set_ylim(10 if log else 0, 100 if log else 8)
    if log:
        ax.set_yscale("log")
    ax.set_xticks([1, 2, 3, 4, 5])
    ax.set_yticks([10, 20, 40, 80] if log else [0, 2, 4, 6, 8])
    fig.canvas.draw()
    width, height = fig.canvas.get_width_height()
    coordinates = ax.transData.transform(np.column_stack([x, y]))
    coordinates[:, 1] = height - coordinates[:, 1]
    xticks = [
        Tick(pixel=float(ax.transData.transform((v, 20 if log else 2))[0]), value=v) for v in [1, 2, 3, 4, 5]
    ]
    yticks = [
        Tick(pixel=float(height - ax.transData.transform((2, v))[1]), value=v)
        for v in ([10, 20, 40, 80] if log else [0, 2, 4, 6, 8])
    ]
    xc = calibrate(xticks)
    yc = calibrate(yticks, "log10" if log else "linear")
    stream = io.BytesIO()
    fig.savefig(stream, format="png")
    plt.close(fig)
    image = Image.open(io.BytesIO(stream.getvalue())).convert("RGB")
    area = (50, 36, 360, 267)
    found, masks = extract_series(image, {"red": [224, 32, 32]}, area, kind)
    points = found["red"]
    assert points
    values = np.array([[xc.value(px), yc.value(py)] for px, py in points])
    if kind in {"bar", "scatter"}:
        assert len(values) == 5
        errors = np.abs(values[:, 1] - y) / y
    else:
        expected = np.interp(np.array([px for px, _ in points]), coordinates[:, 0], coordinates[:, 1])
        expected_values = np.array([yc.value(p) for p in expected])
        errors = np.abs(values[:, 1] - expected_values) / expected_values
    assert np.median(errors) < 0.03
    if kind == "line":
        assert overlap_iou((height, width), points, masks["red"]) > 0.4


def test_axis_outlier_and_invalid_ticks():
    result = calibrate([Tick(pixel=p, value=v) for p, v in [(0, 0), (25, 1), (50, 2), (75, 9), (100, 4)]])
    assert 3 in result.rejected and result.value(50) == pytest.approx(2)
    with pytest.raises(ValueError):
        calibrate([Tick(pixel=1, value=1)])
    with pytest.raises(ValueError):
        calibrate([Tick(pixel=0, value=0), Tick(pixel=1, value=10)], "log10")
