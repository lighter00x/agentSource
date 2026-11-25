"""Owned-document visual reading: figures, full PDF pages and precise zooms."""

from __future__ import annotations

import base64
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from drugpaper.domain.geometry import BBox, Region
from drugpaper.gateway.base import ImagePart
from drugpaper.persistence.models import Document
from drugpaper.render.images import limit_max_side
from drugpaper.render.pages import png_size

VISUAL_POLICY_VERSION = "label-independent-bounded-assembly/6.7"
VISUAL_TOOLS = frozenset({"inspect_visual", "inspect_page", "inspect_crop"})


class ViewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    node_id: str | None = None
    page: int | None = Field(default=None, ge=0)
    region_index: int | None = Field(default=None, ge=0)
    bbox: list[float] | None = Field(default=None, min_length=4, max_length=4)
    max_side: int = Field(default=2000, ge=1024, le=2400)

    @model_validator(mode="after")
    def valid_box(self):
        if self.bbox is not None:
            b = BBox(x0=self.bbox[0], y0=self.bbox[1], x1=self.bbox[2], y1=self.bbox[3])
            if not b.in_bounds() or b.area <= 0:
                raise ValueError("bbox must have positive area within [0,1]")
        return self


async def read_visual(
    agent: Any, state: Any, source: Any, name: str, arguments: dict[str, Any]
) -> tuple[dict[str, Any], list[ImagePart]]:
    args = ViewRequest.model_validate({k: v for k, v in arguments.items() if k != "reason"})
    async with agent.services.db.session(state["user_id"]) as session:
        doc = await session.get(Document, state["document_id"])
        if doc is None or doc.user_id != state["user_id"] or doc.active_revision != source.revision:
            raise PermissionError("document is inaccessible or revision changed")
        pdf_ref = doc.sha256
    regions = []
    node_ref = None
    if name == "inspect_visual":
        if not args.node_id or args.page is not None:
            raise ValueError("inspect_visual requires node_id, not page")
        node = source.node(args.node_id)
        if node.node_type not in {
            "figure",
            "table",
            "scheme",
            "chart",
            "image",
            "figure_part",
            "chart_panel",
        }:
            raise ValueError("Only document figure/table nodes may be inspected")
        node_ref = source.ref(node.local_id)
        regions = list(node.geometry.regions)
        if args.region_index is not None:
            if args.region_index >= len(regions):
                raise ValueError("region_index is outside this figure")
            regions = [regions[args.region_index]]
        elif args.bbox is not None and len(regions) != 1:
            raise ValueError("Select region_index before zooming a multi-page figure")
        if args.bbox is not None:
            r = regions[0]
            x0, y0, x1, y1 = args.bbox
            b = r.bbox
            regions = [
                Region(
                    page=r.page,
                    bbox=BBox(
                        x0=b.x0 + x0 * b.width,
                        y0=b.y0 + y0 * b.height,
                        x1=b.x0 + x1 * b.width,
                        y1=b.y0 + y1 * b.height,
                    ),
                )
            ]
    elif name == "inspect_page":
        if (
            args.page is None
            or args.page not in source.pages()
            or args.node_id is not None
            or args.region_index is not None
        ):
            raise ValueError("inspect_page requires a valid current-document page (0-based)")
        box = args.bbox or [0, 0, 1, 1]
        regions = [Region(page=args.page, bbox=BBox(x0=box[0], y0=box[1], x1=box[2], y1=box[3]))]
    else:
        raise PermissionError("Unknown visual tool")
    if not regions or len(regions) > 8:
        raise ValueError("No visible regions or too many segments; select region_index (maximum 8 images)")
    store = agent.services.store_for(state["user_id"])
    renderer = agent.services.renderer_for(state["user_id"])
    images, refs = [], []
    for r in regions:
        blob = await renderer.crop(pdf_ref, r, dpi=300)
        data, mime = limit_max_side(await store.get(blob), args.max_side)
        bounded_ref = await store.put(data, mime)
        refs.append(
            {
                "blob_ref": bounded_ref,
                "mime": mime,
                "page": r.page,
                "bbox": r.bbox.model_dump(),
                "width": png_size(data)[0],
                "height": png_size(data)[1],
                "max_side": args.max_side,
            }
        )
        images.append(ImagePart(data_b64=base64.b64encode(data).decode(), mime=mime))
    return {
        "ok": True,
        "node_ref": node_ref,
        "view": name,
        "images": len(images),
        "image_refs": refs,
        "page_numbering": "zero_based",
    }, images


def prioritize_view(
    source: Any, state: Any, name: str, arguments: dict[str, Any]
) -> tuple[str, dict[str, Any]]:
    """Apply node → containing page → explicitly justified crop, before rendering."""
    evidence = state.get("exploration", {}).get("evidence", [])
    if name == "inspect_crop":
        allowed = {"source_evidence_id", "bbox", "reason", "max_side"}
        if (
            set(arguments) - allowed
            or not isinstance(arguments.get("reason"), str)
            or not arguments["reason"].strip()
        ):
            raise ValueError("Custom crop requires a source full-page evidence ID and a clear reason")
        prior = next((e for e in evidence if e.get("id") == arguments.get("source_evidence_id")), None)
        if not prior or prior.get("tool") != "inspect_page" or prior.get("arguments", {}).get("bbox"):
            raise ValueError("Inspect a full page first, then select its evidence ID for a fallback crop")
        refs = prior.get("result", {}).get("image_refs", [])
        if len(refs) != 1 or not isinstance(arguments.get("bbox"), list) or len(arguments["bbox"]) != 4:
            raise ValueError("Custom crop needs one full-page source and bbox")
        return "inspect_page", {
            "page": refs[0]["page"],
            "bbox": arguments["bbox"],
            "max_side": arguments.get("max_side", 2000),
        }
    if arguments.get("bbox") is not None:
        raise ValueError(
            "Do not guess bbox in normal viewing tools. Prefer node, then full page; "
            "use inspect_crop with full-page evidence only as a fallback."
        )
    if name == "inspect_page" and isinstance(arguments.get("page"), int):
        page = arguments["page"]
        known = [
            n
            for n in source.ir.nodes
            if n.node_type in {"figure", "table", "scheme", "chart", "image", "figure_part", "chart_panel"}
            and any(r.page == page for r in n.geometry.regions)
        ]
        viewed = {state["node_ref"]}  # Target images are already present in the first model message.
        viewed.update(
            e.get("result", {}).get("node_ref") for e in evidence if e.get("tool") == "inspect_visual"
        )
        if known and not any(source.ref(n.local_id) in viewed for n in known):
            raise ValueError(
                "Prefer existing figure/table nodes before reading this entire page. "
                "Call inspect_visual with one of: " + ", ".join(source.ref(n.local_id) for n in known[:8])
            )
    return name, arguments


async def preview_pages(
    agent: Any, state: Any, source: Any, pages: list[int]
) -> tuple[dict, list[ImagePart]]:
    """Low-resolution navigation only; deliberately does not grant crop/verification privileges."""
    async with agent.services.db.session(state["user_id"]) as session:
        doc = await session.get(Document, state["document_id"])
        if doc is None or doc.user_id != state["user_id"] or doc.active_revision != source.revision:
            raise PermissionError("document is inaccessible or revision changed")
        pdf_ref = doc.sha256
    if not pages or len(pages) != len(set(pages)) or any(p not in source.pages() for p in pages):
        raise ValueError("Choose distinct current-document page indices from get_document_map")
    store = agent.services.store_for(state["user_id"])
    renderer = agent.services.renderer_for(state["user_id"])
    images, refs = [], []
    for page in pages:
        ref = await renderer.render_page(pdf_ref, page, dpi=96)
        data, mime = limit_max_side(await store.get(ref), 1024)
        bounded = await store.put(data, mime)
        refs.append(
            {
                "blob_ref": bounded,
                "mime": mime,
                "page": page,
                "display_page": page + 1,
                "width": png_size(data)[0],
                "height": png_size(data)[1],
                "max_side": 1024,
                "precision": "preview",
            }
        )
        images.append(ImagePart(data_b64=base64.b64encode(data).decode(), mime=mime))
    return {
        "ok": True,
        "view": "preview_pages",
        "precision": "preview",
        "images": len(images),
        "image_refs": refs,
        "note": "Navigation only. Verify chemistry and small text with inspect_visual/page.",
    }, images
