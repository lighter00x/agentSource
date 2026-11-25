"""监控接口回归：范围隔离、分页/筛选、脱敏、检查点版本。使用独立临时库。"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import httpx
import pytest

from drugpaper.api.app import create_app
from drugpaper.api.monitoring import redact
from drugpaper.persistence.db import Database
from drugpaper.persistence.models import Document, ReaderNoteRow, RunRow


def test_redaction_retains_usage_and_marks_truncation() -> None:
    value = redact({"model": {"api_key": "secret", "usage": {"total_tokens": 5}}, "error": "Bearer xyz"})
    assert value == {"model": {"api_key": "[redacted]", "usage": {"total_tokens": 5}}, "error": "[redacted]"}
    assert redact("x" * 16001).endswith("[truncated]")
    assert redact(list(range(201)))[-1] == {"_truncated_items": 1}


@pytest.mark.asyncio
async def test_monitoring_contract(tmp_path) -> None:
    db = Database(f"sqlite+aiosqlite:///{tmp_path / 'monitor.db'}")
    await db.create_all()
    now = datetime.now(UTC)
    async with db.session() as s:
        s.add_all(
            [
                Document(id="doc_a", sha256="a", active_revision=2),
                Document(id="doc_b", sha256="b"),
                Document(id="doc_private", sha256="c", user_id="other"),
                Document(id="doc_deleted", sha256="d", deleted_at=now),
            ]
        )
    async with db.session() as s:
        s.add_all(
            [
                RunRow(
                    id="run_a",
                    document_id="doc_a",
                    run_type="reader",
                    component="reader_read",
                    status="failed",
                    started_at=now,
                    ended_at=now + timedelta(seconds=2),
                    trace_id="trace_a",
                    inputs={"api_key": "private"},
                    metrics={"model_calls": 2},
                    error={"message": "Bearer hidden"},
                ),
                RunRow(
                    id="run_b",
                    document_id="doc_a",
                    run_type="pipeline",
                    component="full_text",
                    status="running",
                    started_at=now - timedelta(seconds=5),
                ),
                RunRow(
                    id="run_other",
                    document_id="doc_a",
                    user_id="other",
                    run_type="reader",
                    component="reader_read",
                    status="failed",
                ),
                RunRow(id="run_foreign", document_id="doc_b", run_type="reader", component="reader_read"),
            ]
        )
        for identity, revision, active, user in [
            ("note_current", 2, True, "default"),
            ("note_old", 1, True, "default"),
            ("note_inactive", 2, False, "default"),
            ("note_other", 2, True, "other"),
        ]:
            s.add(
                ReaderNoteRow(
                    id=identity,
                    document_id="doc_a",
                    revision=revision,
                    page=0,
                    version={"note_current": 1, "note_old": 1, "note_inactive": 2, "note_other": 3}[identity],
                    is_active=active,
                    user_id=user,
                    fingerprint="f",
                    payload={"pages_read": [0]},
                )
            )
    app = create_app(SimpleNamespace(db=db))
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            path = "/api/v1/documents/doc_a"
            page = (await client.get(f"{path}/runs?limit=1")).json()
            assert page["total"] == 2 and len(page["runs"]) == 1
            assert page["runs"][0]["id"] == "run_a"
            assert "inputs" not in page["runs"][0]
            assert page["runs"][0]["duration_ms"] == 2000
            second = (await client.get(f"{path}/runs?limit=1&offset=1")).json()
            assert second["runs"][0]["id"] == "run_b"
            filtered = (
                await client.get(f"{path}/runs?status=failed&component=reader_read&trace_id=trace_a")
            ).json()
            assert filtered["total"] == 1
            detail = (await client.get(f"{path}/runs/run_a")).json()
            assert detail["inputs"]["api_key"] == "[redacted]"
            assert detail["error"]["message"] == "[redacted]"
            for run in ("run_other", "run_foreign", "missing"):
                assert (await client.get(f"{path}/runs/{run}")).status_code == 404
            for doc in ("doc_private", "doc_deleted", "missing"):
                assert (await client.get(f"/api/v1/documents/{doc}/runs")).status_code == 404
                assert (await client.get(f"/api/v1/documents/{doc}/reader-notes")).status_code == 404
            assert (await client.get(f"{path}/runs?limit=201")).status_code == 422
            notes = (await client.get(f"{path}/reader-notes")).json()
            assert notes["total"] == 1 and notes["notes"][0]["id"] == "note_current"
            assert notes["revision"] == 2
    finally:
        await db.dispose()
