"""Persist the actual per-segment images, before/after binding and a contact sheet."""

from __future__ import annotations

import base64
import io
import textwrap
from typing import Any

from PIL import Image, ImageDraw

from drugpaper.skills.protocol import SkillContext
from drugpaper.vision.overlay import comprehensive


async def image_products(ctx: SkillContext, delivery: dict[str, Any]) -> list[dict[str, Any]]:
    if ctx.materials is None or ctx.blobs is None:
        raise ValueError("Molecular delivery requires image material and blob storage")
    products = []
    for segment, part in enumerate(ctx.materials.images):
        if not part.data_b64:
            raise ValueError("Missing source image segment")
        raw = base64.b64decode(part.data_b64)
        image = Image.open(io.BytesIO(raw)).convert("RGB")
        items = [b for b in delivery["coverage"]["items"] if b.get("segment", 0) == segment]
        before = comprehensive(image, {"boxes": items})
        after = image.copy()
        draw = ImageDraw.Draw(after)
        for box in items:
            b = box["bbox"]
            xy = (
                int(b["x0"] * image.width),
                int(b["y0"] * image.height),
                int(b["x1"] * image.width),
                int(b["y1"] * image.height),
            )
            color = (
                "green"
                if box["status"] == "resolved"
                else "orange"
                if box["status"] == "excluded_pending_review"
                else "red"
            )
            draw.rectangle(xy, outline=color, width=3)
            draw.text((xy[0], max(0, xy[1] - 14)), f"#{box['mol_id']} {box['status']}", fill=color)

        async def persist(stage: str, page: int, img: Image.Image, segment_index: int = segment) -> None:
            assert ctx.blobs is not None
            stream = io.BytesIO()
            img.save(stream, format="PNG")
            products.append(
                {
                    "segment": segment_index,
                    "stage": stage,
                    "page": page,
                    "blob_ref": await ctx.blobs.put(stream.getvalue(), "image/png"),
                    "width": img.width,
                    "height": img.height,
                    "status": "pending_content_review",
                    "coverage_status": delivery["coverage"]["status"],
                }
            )

        # Paginate contact sheets and bound their dimensions for concurrent users.
        background = after.copy()
        background.thumbnail((1600, 1600))
        stages = [("original", 0, image), ("before_backfill", 0, before), ("after_backfill", 0, after)]
        for stage, page, img in stages:
            await persist(stage, page, img)
        for page, offset in enumerate(range(0, max(1, len(items)), 8)):
            page_items = items[offset : offset + 8]
            final = Image.new(
                "RGB", (background.width + 400, max(background.height, len(page_items) * 160)), "white"
            )
            final.paste(background, (0, 0))
            fd = ImageDraw.Draw(final)
            for i, box in enumerate(page_items):
                b = box["bbox"]
                top = i * 160
                crop = image.crop(
                    (
                        int(b["x0"] * image.width),
                        int(b["y0"] * image.height),
                        int(b["x1"] * image.width),
                        int(b["y1"] * image.height),
                    )
                )
                crop.thumbnail((110, 120))
                final.paste(crop, (background.width + 8, top + 25))
                matches = [v for v in delivery["bindings"] if v["mol_id"] == box["mol_id"]]
                text = f"#{box['mol_id']} {box['status']}\n" + (
                    matches[0]["e_smiles_raw"] if matches else "No verified binding"
                )
                fd.text(
                    (background.width + 125, top + 8),
                    "\n".join(textwrap.wrap(text, width=38))[:500],
                    fill="black",
                )
            await persist("final", page, final)
    return products
