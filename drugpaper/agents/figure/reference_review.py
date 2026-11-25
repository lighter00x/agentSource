"""Bounded independent audit of proposed assignments against their actual sources."""

from __future__ import annotations

import base64
import copy
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from drugpaper.agents.figure.context import pointer, structure_placeholder_inventory
from drugpaper.agents.figure.member_normalization import normalization_checks
from drugpaper.agents.figure.workflow import EditRejected, digest
from drugpaper.gateway.base import ImagePart


class ReferenceIssue(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    assignment_index: int = Field(ge=0)
    field: Literal["reference", "substituents", "position_definitions", "evidence_text"]
    description: str = Field(min_length=1)
    source_evidence: str = Field(min_length=1)
    suggested_action: str = Field(min_length=1)


class ReferenceReview(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    status: Literal["passed", "needs_revision", "uncertain"]
    issues: list[ReferenceIssue] = Field(default_factory=list, max_length=32)
    summary: str = Field(min_length=1)


async def review_reference_assignments(agent, state: dict, arguments: dict) -> dict:
    log = state["exploration"]
    target = (
        pointer(state.get("extraction", {}), arguments["product_path"])
        if arguments.get("product_path")
        else {}
    )
    checks = normalization_checks(target, arguments["assignments"])
    _, ctx = await agent._ctx(state)
    images = list(ctx.materials.images)
    evidence = {e["id"]: e for e in log.get("evidence", [])}
    sources = []
    seen = set()
    for eid in arguments["evidence_ids"]:
        result = evidence[eid]["result"]
        slots = []
        for item in result["image_refs"]:
            blob = item["blob_ref"]
            if blob not in seen:
                seen.add(blob)
                slots.append(len(images))
                images.append(
                    ImagePart(
                        blob_ref=blob,
                        mime=item.get("mime", "image/png"),
                        data_b64=base64.b64encode(
                            await agent.services.store_for(state["user_id"]).get(blob)
                        ).decode(),
                    )
                )
            else:
                slots.append(next(i for i, image in enumerate(images) if image.blob_ref == blob))
        sources.append(
            {
                "evidence_id": eid,
                "node_ref": result.get("node_ref"),
                "image_indices": slots,
                "pages": sorted({item["page"] for item in result["image_refs"] if "page" in item}),
            }
        )
    if len(images) > agent.services.settings.figure_exploration_max_context_images:
        raise EditRejected(
            "Too many source images for one assignment review",
            {
                "code": "reference_review_context",
                "action": "Use a smaller assignment batch with only its necessary detailed source evidence IDs",
            },
        )
    fingerprint = digest(
        {
            "arguments": arguments,
            "sources": sources,
            "inventory": structure_placeholder_inventory(ctx.materials),
            "normalization_checks": checks,
        }
    )
    reviews = log.setdefault("reference_reviews", [])
    cached = next(
        (r for r in reviews if r.get("fingerprint") == fingerprint and r.get("status") != "review_error"),
        None,
    )
    if cached is not None:
        review = copy.deepcopy(cached)
    else:
        if len(reviews) >= 6:
            raise EditRejected(
                "Reference source review budget exhausted",
                {
                    "code": "reference_review_budget",
                    "action": "Retain unsupported proposals for review; do not bypass source verification",
                },
            )
        # Reserve an attempt even when the service fails, within the same global timeout.
        review = {"fingerprint": fingerprint, "status": "review_error", "sources": sources}
        reviews.append(review)
        result = await agent._run_skill(
            state,
            "reference_assignment_audit",
            {
                "proposal": arguments,
                "sources": sources,
                "structure_inventory": structure_placeholder_inventory(ctx.materials),
                "target_image_count": len(ctx.materials.images),
                "normalization_checks": checks,
            },
            images=images,
            category="exploration",
            force=True,
        )
        parsed = ReferenceReview.model_validate(result)
        if any(issue.assignment_index >= len(arguments["assignments"]) for issue in parsed.issues):
            raise ValueError("Source review returned an unknown assignment index")
        review.update(parsed.model_dump())
        if parsed.issues and review["status"] == "passed":
            review["status"] = "needs_revision"
        from drugpaper.agents.figure.process_trace import image_references

        image_refs = await image_references(agent.services.store_for(state["user_id"]), images)
        metadata = {
            item["blob_ref"]: {
                **item,
                "evidence_id": eid,
                "node_ref": evidence[eid]["result"].get("node_ref"),
                "role": "reference",
            }
            for eid in arguments["evidence_ids"]
            for item in evidence[eid]["result"]["image_refs"]
        }
        event = {
            "kind": "reference_review",
            **copy.deepcopy(review),
            "parent_call_id": log.get("active_call_id"),
            "turn": log.get("active_turn", 0),
            "image_refs": [
                {**ref, **metadata.get(ref["blob_ref"], {"role": "target"})} for ref in image_refs
            ],
        }
        log.setdefault("steps", []).append(event)
        await agent._exploration_event(state, event)
    if review["status"] != "passed":
        raise EditRejected(
            "Source images do not yet verify these member assignments",
            {
                "code": "reference_assignment_review_failed",
                "review": review,
                "action": "Correct only the reported assignment fields using the source image, or inspect a more precise source if unclear. Then preview/submit the corrected proposal; do not regenerate the figure or guess missing identities.",
            },
        )
    return copy.deepcopy(review)
