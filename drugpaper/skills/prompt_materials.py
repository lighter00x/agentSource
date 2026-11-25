"""Project a bounded task view; full tool material never becomes the model catalog."""

from __future__ import annotations

from typing import Any


def check_evidence_request(request: Any, ctx: Any) -> Any:
    """Do not silently discard selected evidence or target segments at extraction time."""
    from drugpaper.gateway.base import ImagePart

    limits = ctx.extras.get("figure_context_limits")
    if limits:
        count = sum(
            isinstance(p, ImagePart)
            for m in request.messages
            if isinstance(m.content, list)
            for p in m.content
        )
        import json

        size = len(request.model_dump_json(exclude={"response_schema"}).encode())
        if request.response_schema is not None:
            size += len(json.dumps(request.response_schema.model_json_schema()).encode())
        if count > limits["images"] or size > limits["bytes"]:
            raise ValueError(
                "Extraction context budget exceeded; select_context with fewer relevant evidence IDs, then retry"
            )
    return request


def project_target(name: str, target: dict[str, Any]) -> dict[str, Any]:
    out = {k: target.get(k) for k in ("ref", "type", "caption", "regions", "subtype")}
    if name in {"visual_classify", "visual_reclassify", "figure_self_check"}:
        return out
    out["boxes"] = [
        {
            "mol_id": n.get("attributes", {}).get("reading_index"),
            "bbox": n.get("geometry", {}).get("local_bbox"),
            "segment": n.get("geometry", {}).get("parent_region_index", 0),
        }
        for n in target.get("children", [])
        if n.get("status") != "removed"
    ]
    board = target.get("blackboard", {})
    out["blackboard_excerpt"] = {
        "title": board.get("title"),
        "labels": board.get("labels", [])[:160],
        "references": [str(t)[:1600] for t in board.get("references", {}).get(target.get("ref"), [])[:4]],
    }
    return out


async def document_evidence_images(ctx: Any) -> list:
    """Pass retrieved visuals to both prompt and Python extraction Skills."""
    import base64

    from drugpaper.gateway.base import ImagePart, TextPart
    from drugpaper.render.images import limit_max_side

    parts = []
    for evidence in ctx.extras.get("document_evidence_images", []):
        if evidence.get("tool") not in {"inspect_visual", "inspect_page", "inspect_crop"}:
            continue
        result = evidence.get("result", {})
        for image in result.get("image_refs", []):
            if ctx.blobs is None:
                raise ValueError("Related image evidence requires a blob store")
            parts.append(
                TextPart(
                    text="Related evidence image (not the extraction target; its molecule IDs "
                    "must not replace target IDs): "
                    + str(
                        {
                            "evidence_id": evidence.get("id"),
                            "tool": evidence.get("tool"),
                            "node_ref": result.get("node_ref"),
                            "page": image.get("page"),
                            "bbox": image.get("bbox"),
                        }
                    )
                )
            )
            data, mime = limit_max_side(
                await ctx.blobs.get(image["blob_ref"]), min(2400, image.get("max_side", 1600))
            )
            parts.append(ImagePart(data_b64=base64.b64encode(data).decode(), mime=mime))
    return parts
