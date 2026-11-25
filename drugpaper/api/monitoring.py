"""只读开发监控；沿用 P6 default 用户边界，不推断未持久化的执行事件。"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Query
from sqlalchemy import func, select

from drugpaper.api.deps import SessionDep, require_document
from drugpaper.api.errors import not_found
from drugpaper.persistence.models import ReaderNoteRow, RunRow

router = APIRouter(tags=["monitoring"])
_SECRET = re.compile(r"api[_-]?key|authorization|password|secret|access[_-]?token|admin[_-]?token", re.I)
_BEARER = re.compile(r"(?i)bearer\s+[^\s\"']+|\bsk-[A-Za-z0-9_-]{8,}")


def redact(value: Any, depth: int = 0) -> Any:
    """调试响应有限长；屏蔽已知凭据键与常见令牌字符串，不改变 usage token 统计。"""
    if depth > 16:
        return "[truncated: depth]"
    if isinstance(value, dict):
        result = {
            str(k): "[redacted]" if _SECRET.search(str(k)) else redact(v, depth + 1)
            for k, v in list(value.items())[:200]
        }
        if len(value) > 200:
            result["_truncated_keys"] = len(value) - 200
        return result
    if isinstance(value, list):
        items = [redact(v, depth + 1) for v in value[:200]]
        if len(value) > 200:
            items.append({"_truncated_items": len(value) - 200})
        return items
    if isinstance(value, str):
        clean = _BEARER.sub("[redacted]", value)
        return clean[:16000] + (" [truncated]" if len(clean) > 16000 else "")
    return value


def summary(row: RunRow) -> dict[str, Any]:
    start = row.started_at.replace(tzinfo=UTC) if row.started_at.tzinfo is None else row.started_at
    end = row.ended_at
    if end is not None and end.tzinfo is None:
        end = end.replace(tzinfo=UTC)
    return {
        "id": row.id,
        "document_id": row.document_id,
        "run_type": row.run_type,
        "component": row.component,
        "component_version": row.component_version,
        "parent_run_id": row.parent_run_id,
        "trace_id": row.trace_id,
        "status": row.status,
        "error": redact(row.error),
        "metrics": redact(row.metrics),
        "started_at": start.isoformat(),
        "ended_at": end.isoformat() if end else None,
        "duration_ms": round(((end or datetime.now(UTC)) - start).total_seconds() * 1000),
    }


async def visible(s: SessionDep, document_id: str) -> Any:
    doc = await require_document(s, document_id)
    if doc.user_id != "default" or doc.deleted_at is not None:
        raise not_found("文档", document_id)
    return doc


@router.get("/documents/{document_id}/runs")
async def runs(
    s: SessionDep,
    document_id: str,
    status: str | None = None,
    component: str | None = None,
    trace_id: str | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> dict[str, Any]:
    await visible(s, document_id)
    filters = [RunRow.document_id == document_id, RunRow.user_id == "default"]
    for column, value in (
        (RunRow.status, status),
        (RunRow.component, component),
        (RunRow.trace_id, trace_id),
    ):
        if value is not None:
            filters.append(column == value)
    total = await s.scalar(select(func.count()).select_from(RunRow).where(*filters))
    rows = (
        (
            await s.execute(
                select(RunRow)
                .where(*filters)
                .order_by(RunRow.started_at.desc(), RunRow.id.desc())
                .offset(offset)
                .limit(limit)
            )
        )
        .scalars()
        .all()
    )
    return {
        "runs": [summary(row) for row in rows],
        "total": total,
        "limit": limit,
        "offset": offset,
        "observed_at": datetime.now(UTC).isoformat(),
        "scope": "persisted_history_all_revisions",
    }


@router.get("/documents/{document_id}/runs/{run_id}")
async def run_detail(s: SessionDep, document_id: str, run_id: str) -> dict[str, Any]:
    await visible(s, document_id)
    row = await s.get(RunRow, run_id)
    if row is None or row.document_id != document_id or row.user_id != "default":
        raise not_found("运行", run_id)
    return {
        **summary(row),
        "component_hash": row.component_hash,
        "inputs": redact(row.inputs),
        "outputs": redact(row.outputs),
        "model": redact(row.model),
        "debug_notice": "已屏蔽常见凭据，长字段有截断标记；分享前请人工检查文献内容。",
    }


@router.get("/documents/{document_id}/reader-notes")
async def reader_notes(
    s: SessionDep,
    document_id: str,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> dict[str, Any]:
    doc = await visible(s, document_id)
    filters = [
        ReaderNoteRow.document_id == document_id,
        ReaderNoteRow.user_id == "default",
        ReaderNoteRow.revision == doc.active_revision,
        ReaderNoteRow.is_active.is_(True),
    ]
    total = await s.scalar(select(func.count()).select_from(ReaderNoteRow).where(*filters))
    rows = (
        (
            await s.execute(
                select(ReaderNoteRow).where(*filters).order_by(ReaderNoteRow.page).offset(offset).limit(limit)
            )
        )
        .scalars()
        .all()
    )
    return {
        "revision": doc.active_revision,
        "total": total,
        "notes": [
            {
                "id": row.id,
                "page": row.page,
                "version": row.version,
                "fingerprint": row.fingerprint,
                "created_at": row.created_at,
                "payload": redact(row.payload),
            }
            for row in rows
        ],
        "notice": "当前 revision 的 active 检查点；未重新认证源指纹与内容正确性。",
    }
