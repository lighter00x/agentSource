from __future__ import annotations

import asyncio
from typing import Any

from drugpaper.workers.celery_app import app


@app.task(name="drugpaper.understand_full_text", acks_late=True, reject_on_worker_lost=True)  # type: ignore[untyped-decorator]
def understand_full_text(
    document_id: str, user_id: str = "default", from_page: int | None = None
) -> dict[str, Any]:
    async def run() -> dict[str, Any]:
        from drugpaper.orchestration.full_text_pipeline import FullTextPipeline
        from drugpaper.services import Services

        services = Services.from_settings(user_id=user_id)
        try:
            return await FullTextPipeline(services).run(document_id, user_id=user_id, from_page=from_page)
        finally:
            await services.aclose()

    return asyncio.run(run())


async def process(document_id: str, user_id: str = "default", batch_id: str | None = None) -> dict[str, Any]:
    from drugpaper.orchestration.figure_pipeline import FigurePipeline
    from drugpaper.services import Services

    sv = Services.from_settings()
    try:
        from drugpaper.persistence.models import Document

        async with sv.db.session(user_id) as session:
            doc = await session.get(Document, document_id)
            if doc is None or doc.user_id != user_id:
                raise PermissionError("document task ownership mismatch")
            pending = doc.status != "ready"
            sha, filename = doc.sha256, doc.filename
        if pending:
            from drugpaper.orchestration.pipeline_graph import DocumentPipeline

            pdf = await sv.store_for(user_id).get(sha)
            ingested = await DocumentPipeline(sv, understand_figures=True).run(
                pdf=pdf, filename=filename, user_id=user_id
            )
            result = ingested.figure_understanding or {
                "document_id": document_id,
                "status": "failed",
                "calls": 0,
                "error": ingested.error,
            }
        else:
            result = await FigurePipeline(sv).run(document_id, user_id=user_id)
        if batch_id:
            from sqlalchemy import select

            from drugpaper.persistence.models import BatchRow

            async with sv.db.session(user_id) as session:
                batch = (
                    await session.execute(
                        select(BatchRow)
                        .where(BatchRow.id == batch_id, BatchRow.user_id == user_id)
                        .with_for_update()
                    )
                ).scalar_one_or_none()
                if batch is None or document_id not in batch.payload.get("document_ids", []):
                    raise PermissionError("batch task ownership mismatch")
                results = {
                    **batch.payload.get("results", {}),
                    document_id: {"status": result["status"], "calls": result["calls"]},
                }
                batch.payload = {**batch.payload, "results": results}
                batch.status = (
                    "completed"
                    if len(results) == len(batch.payload["document_ids"])
                    and all(r["status"] == "completed" for r in results.values())
                    else "failed"
                    if len(results) == len(batch.payload["document_ids"])
                    else "running"
                )
        return result
    except Exception as exc:
        if batch_id:
            from sqlalchemy import select

            from drugpaper.persistence.models import BatchRow

            async with sv.db.session(user_id) as session:
                batch = (
                    await session.execute(
                        select(BatchRow)
                        .where(BatchRow.id == batch_id, BatchRow.user_id == user_id)
                        .with_for_update()
                    )
                ).scalar_one_or_none()
                if batch and document_id in batch.payload.get("document_ids", []):
                    results = {
                        **batch.payload.get("results", {}),
                        document_id: {"status": "failed", "error": type(exc).__name__},
                    }
                    batch.payload = {**batch.payload, "results": results}
                    batch.status = (
                        "failed" if len(results) == len(batch.payload["document_ids"]) else "running"
                    )
        raise
    finally:
        await sv.aclose()


@app.task(name="drugpaper.process_document", acks_late=True, reject_on_worker_lost=True)  # type: ignore[untyped-decorator]
def process_document(
    document_id: str, user_id: str = "default", batch_id: str | None = None
) -> dict[str, Any]:
    return asyncio.run(process(document_id, user_id, batch_id))
