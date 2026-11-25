from __future__ import annotations

import re
from typing import Any

from PIL import Image, ImageDraw


def comprehensive(image: Image.Image, ctx: dict[str, Any]) -> Image.Image:
    out = image.convert("RGBA")
    draw = ImageDraw.Draw(out, "RGBA")
    w, h = out.size
    for box in ctx.get("boxes", []):
        b = box["bbox"]
        coords = (b["x0"] * w, b["y0"] * h, b["x1"] * w, b["y1"] * h)
        draw.rectangle(coords, outline="red", width=2)
        draw.text(
            (coords[0], coords[1]), str(box["mol_id"]) + ": " + str(box.get("reference", "?")), fill="blue"
        )
    data = ctx.get("extraction", {})
    for row in data.get("rows", []):
        for cell in row:
            b = cell.get("bbox")
            if b:
                xy = (b["x0"] * w, b["y0"] * h)
                text = str(cell.get("text", ""))
                draw.rectangle((*xy, xy[0] + max(20, len(text) * 6), xy[1] + 12), fill=(255, 255, 0, 100))
                draw.text(xy, text, fill="black")
    for axis_name in ("x_axis", "y_axis"):
        for tick in data.get(axis_name, {}).get("ticks", []):
            p = tick["pixel"]
            if axis_name == "x_axis":
                draw.line((p, 0, p, h), fill=(0, 200, 0, 80))
                draw.text((p, h - 14), str(tick["value"]), fill="green")
            else:
                draw.line((0, p, w, p), fill=(0, 200, 0, 80))
                draw.text((0, p), str(tick["value"]), fill="green")
    for series in data.get("series", []):
        for point in series.get("points_px", []):
            if len(point) < 2:
                continue
            x, y = point[:2]
            draw.ellipse((x - 2, y - 2, x + 2, y + 2), outline="magenta", width=2)
    return out.convert("RGB")


def crop_check(image: Image.Image, payload: dict[str, Any], field_path: str) -> Image.Image | None:
    indices = re.fullmatch(r"rows\[(\d+)\]\[(\d+)\]", field_path)
    if not indices:
        return None
    try:
        box = payload["rows"][int(indices[1])][int(indices[2])].get("bbox")
    except (IndexError, KeyError):
        return None
    if not box:
        return None
    return image.crop(
        (
            int(box["x0"] * image.width),
            int(box["y0"] * image.height),
            int(box["x1"] * image.width),
            int(box["y1"] * image.height),
        )
    )
