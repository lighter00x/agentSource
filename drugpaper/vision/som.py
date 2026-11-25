from __future__ import annotations

import io
from typing import Any

from PIL import Image, ImageDraw, ImageFont

from drugpaper.domain.geometry import BBox, norm_to_px


def png(image: Image.Image) -> bytes:
    out = io.BytesIO()
    image.save(out, "PNG")
    return out.getvalue()


def crop_masked(data: bytes, box: BBox, masks: list[BBox]) -> bytes:
    with Image.open(io.BytesIO(data)) as source:
        image = source.convert("RGB").crop(norm_to_px(box, *source.size))
    draw = ImageDraw.Draw(image)
    for mask in masks:
        draw.rectangle(norm_to_px(mask, *image.size), fill="white")
    return png(image)


def render(data: bytes, boxes: list[dict[str, Any]]) -> bytes:
    with Image.open(io.BytesIO(data)) as source:
        image = source.convert("RGB")
    draw = ImageDraw.Draw(image)
    width = max(1, max(image.size) // 500)
    font = ImageFont.load_default(size=max(16, min(64, max(image.size) // 40)))
    for item in boxes:
        box = BBox.model_validate(item["local_bbox"])
        pixels = norm_to_px(box, *image.size)
        draw.rectangle(pixels, outline="red", width=width)
        label = str(item["reading_index"])
        text_box = draw.textbbox((0, 0), label, font=font)
        w, h = text_box[2] - text_box[0] + 6, text_box[3] - text_box[1] + 6
        x, y = max(0, min(pixels[2], image.width - w)), max(0, pixels[1] - h)
        draw.rectangle((x, y, x + w, y + h), fill="white", outline="white", width=2)
        draw.text((x + 3 - text_box[0], y + 3 - text_box[1]), label, fill="blue", font=font)
    return png(image)
