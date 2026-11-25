"""One tool-free vision request per requested page, with a hard wall-clock bound."""

from __future__ import annotations

import asyncio
import base64
import io
from typing import Any

from PIL import Image
from sqlalchemy import select

from drugpaper.agents.lookup.models import LookupAnswers, LookupQuestion, LookupResult
from drugpaper.facts.full_text import FullTextContext, valid_evidence
from drugpaper.gateway.base import ChatRequest, ImagePart, Message, ModelClient, TextPart
from drugpaper.persistence.models import Asset
from drugpaper.services import Services


def _resize(data: bytes) -> bytes:
    with Image.open(io.BytesIO(data)) as img:
        img.thumbnail((1600, 1600))
        out = io.BytesIO()
        img.convert("RGB").save(out, format="PNG")
        return out.getvalue()


async def vision_page(
    services: Services,
    model: ModelClient,
    ctx: FullTextContext,
    page: int,
    questions: list[LookupQuestion],
    model_records: list[dict[str, Any]] | None = None,
) -> list[LookupResult]:
    if page not in {p.page for p in ctx.ir.pages}:
        raise ValueError("vision page outside document")
    async with asyncio.timeout(60):
        async with services.db.session(ctx.user_id) as session:
            asset = (
                await session.execute(
                    select(Asset)
                    .where(
                        Asset.document_id == ctx.ir.document_id,
                        Asset.user_id == ctx.user_id,
                        Asset.kind == "origin_pdf",
                    )
                    .limit(1)
                )
            ).scalar_one_or_none()
            if asset is None:
                raise ValueError("vision requires original PDF")
            pdf_ref = asset.blob_ref
        blob = await services.renderer_for(ctx.user_id).render_page(pdf_ref, page)
        data = await services.store_for(ctx.user_id).get(blob)
        data = await asyncio.to_thread(_resize, data)
        refs = [n.ref for n in ctx.ir.nodes if n.page == page and n.status != "removed"]
        prompt = (
            "只回答本页问题；看不清返回not_found。引用只能来自本页节点，不推测图中文字/测定。每题答案不超过150字。\n"
            + str({"questions": [q.model_dump() for q in questions], "page_nodes": refs})
        )
        response = await model.chat(
            ChatRequest(
                messages=[
                    Message(
                        role="user",
                        content=[TextPart(text=prompt), ImagePart(data_b64=base64.b64encode(data).decode())],
                    )
                ],
                response_schema=LookupAnswers,
                max_tokens=2048,
                timeout_s=60,
            ),
            task="vision_lookup",
        )
        if model_records is not None:
            model_records.append(
                {
                    "task": "vision_lookup",
                    "model": response.model,
                    "usage": response.usage.model_dump(),
                    "page": page,
                }
            )
        answers = LookupAnswers.model_validate(response.parsed or {})
        output = []
        for q in questions:
            candidates = [r for r in answers.results if r.question == q.question]
            answer = candidates[0] if len(candidates) == 1 else LookupResult(question=q.question)
            answer.evidence = [e for e in answer.evidence if e.node_id in refs and valid_evidence(ctx.ir, e)]
            if answer.status == "found" and not answer.evidence:
                answer = LookupResult(
                    question=q.question, status="not_found", answer="页面未提供可核验的证据。"
                )
            output.append(answer)
        return output
