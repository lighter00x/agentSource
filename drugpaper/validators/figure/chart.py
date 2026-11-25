from __future__ import annotations

from typing import Any

from drugpaper.facts.figure_models import TableGrid
from drugpaper.facts.table_processing import expand_rows, headers
from drugpaper.validators.figure.codes import CheckResult, issue


def check(ctx: dict[str, Any]) -> list[CheckResult]:
    data = ctx.get("extraction", {})
    out = []
    if ctx.get("expected_panels") is not None and len(data.get("panels", [])) != ctx["expected_panels"]:
        out.append(issue("C_PANEL_COUNT", "panels"))
    if "header_rows" in data:
        grid = TableGrid.model_validate(data)
        try:
            width = len(headers(grid)) if grid.header_rows else (len(grid.rows[0]) if grid.rows else 0)
            if any(len(row) != width for row in expand_rows(grid.rows)):
                out.append(issue("C_HEADER_SPAN", "rows"))
        except ValueError:
            out.append(issue("C_HEADER_SPAN", "rows"))
    if "x_axis" in data:
        for name in ("x", "y"):
            axis = data.get(name + "_axis", {})
            cal = data.get("calibration", {}).get(name, {})
            if cal.get("residual", 1) > 0.02:
                out.append(issue("C_AXIS_RESIDUAL", name + "_axis"))
            if axis.get("scale") == "log10" and any(t.get("value", 0) <= 0 for t in axis.get("ticks", [])):
                out.append(issue("C_AXIS_SCALE", name + "_axis"))
        if data.get("legend_count") is not None and len(data.get("series", [])) != data["legend_count"]:
            out.append(issue("C_LEGEND_MISMATCH", "series"))
        if data.get("overlap_iou") is not None and data["overlap_iou"] < 0.6:
            out.append(issue("C_OVERLAY_IOU_LOW", "series"))
    routes = data.get("relations", [])
    labels = set(ctx.get("route_labels", []))
    if any(edge.get("source") not in labels or edge.get("target") not in labels for edge in routes):
        out.append(issue("C_ROUTE_TOPOLOGY", "relations"))
    return out
