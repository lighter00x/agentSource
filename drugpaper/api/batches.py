from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from drugpaper.api.errors import ApiError
from drugpaper.domain.ids import new_id
from drugpaper.persistence.models import BatchRow, Document

router = APIRouter()


class BatchInput(BaseModel):
    document_ids: list[str] = Field(min_length=1, max_length=128)
    queue: Literal["batch", "interactive"] = "batch"


@router.post("/batches", status_code=202)
async def create_batch(request: Request, body: BatchInput) -> dict[str, Any]:
    sv = request.app.state.services
    ids = list(dict.fromkeys(body.document_ids))
    batch_id = new_id("batch")
    async with sv.db.session("default") as session:
        for document_id in ids:
            doc = await session.get(Document, document_id)
            if doc is None or doc.user_id != "default" or doc.status != "ready":
                raise HTTPException(404, "ready document not found")
        session.add(
            BatchRow(
                id=batch_id,
                user_id="default",
                status="queued",
                payload={"document_ids": ids, "queue": body.queue, "results": {}},
            )
        )
    from drugpaper.workers.tasks import process_document

    task_ids = []
    try:
        for document_id in ids:
            task = await __import__("asyncio").to_thread(
                process_document.apply_async, args=[document_id, "default", batch_id], queue=body.queue
            )
            task_ids.append(task.id)
    except Exception:
        async with sv.db.session("default") as session:
            batch = await session.get(BatchRow, batch_id)
            batch.status = "dispatch_failed"
            batch.payload = {**batch.payload, "task_ids": task_ids}
        raise ApiError(
            503, "QUEUE_UNAVAILABLE", "batch dispatch failed; inspect status before retry", batch_id=batch_id
        ) from None
    async with sv.db.session("default") as session:
        batch = (
            await session.execute(
                __import__("sqlalchemy").select(BatchRow).where(BatchRow.id == batch_id).with_for_update()
            )
        ).scalar_one()
        batch.payload = {**batch.payload, "task_ids": task_ids}
    return {"batch_id": batch_id, "status": "queued", "task_ids": task_ids}


@router.get("/batches/{batch_id}")
async def batch_status(request: Request, batch_id: str) -> dict[str, Any]:
    async with request.app.state.services.db.session("default") as session:
        batch = await session.get(BatchRow, batch_id)
        if batch is None or batch.user_id != "default":
            raise HTTPException(404, "batch not found")
        return {"batch_id": batch_id, "status": batch.status, **batch.payload}
