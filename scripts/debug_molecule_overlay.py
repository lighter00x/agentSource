"""把分子页面坐标画回 144 dpi 页面，保留待复核节点。"""

from __future__ import annotations

import argparse
import asyncio
import io
from pathlib import Path

from PIL import Image, ImageDraw

from drugpaper.docsrc.ir_source import IrDocumentSource
from drugpaper.domain.geometry import norm_to_px
from drugpaper.persistence.models import Document
from drugpaper.services import Services
from drugpaper.vision.som import png


async def overlay(
    services: Services, document_id: str, output: Path, *, user_id: str = "default"
) -> list[str]:
    async with services.db.session(user_id) as session:
        document = await session.get(Document, document_id)
        if document is None or document.user_id != user_id:
            raise PermissionError("文档不可见")
        pdf = document.sha256
    source = await IrDocumentSource.load(services.db, document_id)
    store = services.store_for(user_id)
    await asyncio.to_thread(output.mkdir, parents=True, exist_ok=True)
    paths = []
    for page in source.ir.pages:
        ref = await services.renderer_for(user_id).render_page(pdf, page.page, dpi=144)
        image = Image.open(io.BytesIO(await store.get(ref))).convert("RGB")
        draw = ImageDraw.Draw(image)
        for node in source.ir.of_type("molecule"):
            if node.status == "removed":
                continue
            for region in node.geometry.regions:
                if region.page == page.page:
                    box = norm_to_px(region.bbox, *image.size)
                    draw.rectangle(box, outline="red", width=2)
                    draw.text(
                        (box[0], max(0, box[1] - 15)), str(node.attributes["reading_index"]), fill="blue"
                    )
        path = output / f"page_{page.page:02d}.png"
        path.write_bytes(png(image))
        paths.append(str(path))
    return paths


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--doc", required=True)
    parser.add_argument("--output", type=Path, default=Path("samples/outputs/phase-4/overlay"))
    args = parser.parse_args()
    services = Services.from_settings()
    try:
        print(await overlay(services, args.doc, args.output))
    finally:
        await services.aclose()


if __name__ == "__main__":
    asyncio.run(main())
