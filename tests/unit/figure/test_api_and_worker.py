from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from drugpaper.api.app import create_app
from drugpaper.config.settings import Settings
from drugpaper.persistence.models import BatchRow, Document
from drugpaper.services import Services
from drugpaper.workers import tasks


def test_batch_queue_read_ownership_and_dispatch_failure(tmp_path, monkeypatch):
    sv = Services.from_settings(
        Settings(
            _env_file=None,
            database_url=f"sqlite+aiosqlite:///{tmp_path}/db",
            blob_root=tmp_path / "blobs",
            skills_root=tmp_path / "skills",
            redis_url="",
            skills_watch_enabled=False,
        )
    )
    sent = []

    def dispatch(*, args, queue):
        sent.append((args, queue))
        return SimpleNamespace(id="task-1")

    monkeypatch.setattr(tasks.process_document, "apply_async", dispatch)
    with TestClient(create_app(sv)) as client:

        async def seed():
            async with sv.db.session() as session:
                session.add_all(
                    [
                        Document(id="doc_ready", user_id="default", sha256="a" * 64, status="ready"),
                        Document(id="doc_other", user_id="other", sha256="b" * 64, status="ready"),
                    ]
                )

        client.portal.call(seed)
        response = client.post(
            "/api/v1/batches", json={"document_ids": ["doc_ready", "doc_ready"], "queue": "interactive"}
        )
        assert response.status_code == 202, response.text
        bid = response.json()["batch_id"]
        assert sent == [(["doc_ready", "default", bid], "interactive")]
        assert client.get("/api/v1/batches/" + bid).json()["document_ids"] == ["doc_ready"]
        assert client.post("/api/v1/batches", json={"document_ids": ["doc_other"]}).status_code == 404
        assert client.get("/api/v1/documents/doc_other/measurements").status_code == 404
        assert client.get("/api/v1/documents/doc_ready/figure-reports").json() == []

        def failed(**kw):
            raise ConnectionError("broker unavailable")

        monkeypatch.setattr(tasks.process_document, "apply_async", failed)
        failure = client.post("/api/v1/batches", json={"document_ids": ["doc_ready"]})
        assert failure.status_code == 503
        status = client.get("/api/v1/batches/" + failure.json()["error"]["details"]["batch_id"]).json()
        assert status["status"] == "dispatch_failed"
        client.portal.call(sv.aclose)


@pytest.mark.parametrize("fail", [False, True])
async def test_worker_records_final_batch_result(figure_env, monkeypatch, fail):
    from drugpaper.orchestration.figure_pipeline import FigurePipeline

    sv, _, _, _ = figure_env
    async with sv.db.session() as session:
        doc = await session.get(Document, "doc_chem")
        doc.status = "ready"
        session.add(
            BatchRow(
                id="batch_test",
                user_id="default",
                status="queued",
                payload={"document_ids": ["doc_chem"], "results": {}},
            )
        )
    monkeypatch.setattr(Services, "from_settings", lambda *args, **kw: sv)

    async def run(self, *args, **kw):
        if fail:
            raise RuntimeError("figure infrastructure unavailable")
        return {"document_id": "doc_chem", "status": "completed", "calls": 3}

    monkeypatch.setattr(FigurePipeline, "run", run)
    if fail:
        with pytest.raises(RuntimeError):
            await tasks.process("doc_chem", batch_id="batch_test")
    else:
        assert (await tasks.process("doc_chem", batch_id="batch_test"))["calls"] == 3
    async with sv.db.session() as session:
        batch = await session.get(BatchRow, "batch_test")
        assert batch.status == ("failed" if fail else "completed")
        assert batch.payload["results"]["doc_chem"]["status"] == batch.status
