from __future__ import annotations

import base64

import httpx

from drugpaper.detection.api import create_app
from drugpaper.detection.service import MoleculeDetectionService
from tests.unit.detection.test_service import FakeBackend, png, settings


async def test_worker_batch_files_health_and_bad_image_positions():
    cfg = settings(yolo_service_token="test-token")
    backend = FakeBackend()
    worker = MoleculeDetectionService(backend, cfg)
    app = create_app(cfg, worker)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://worker") as web:
        payload = {
            "user_id": "a",
            "images": [
                base64.b64encode(png()).decode(),
                "!",
                base64.b64encode(b"bad-image").decode(),
                base64.b64encode(png("blue")).decode(),
            ],
        }
        assert (await web.post("/detect/batch", json=payload)).status_code == 403
        response = await web.post("/detect/batch", json=payload, headers={"X-Service-Token": "test-token"})
        results = response.json()["results"]
        assert [r["input_index"] for r in results] == [0, 1, 2, 3]
        assert [len(r["boxes"]) for r in results] == [1, 0, 0, 1]
        assert results[0]["boxes"][0]["confidence"] == 0.9
        assert results[0]["boxes"][0]["cls"] == 0
        uploaded = await web.post(
            "/detect/files",
            files=[("files", ("a.png", png(), "image/png")), ("files", ("bad.png", b"bad", "image/png"))],
            data={"user_id": "a"},
            headers={"X-Service-Token": "test-token"},
        )
        assert [len(r["boxes"]) for r in uploaded.json()["results"]] == [1, 0]
        health = (await web.get("/health")).json()
        assert health["queue_size"] == 0 and health["device"] == "cpu"
    await worker.aclose()


async def test_startup_warms_model_once_and_shutdown_closes_backend(monkeypatch):
    backend = FakeBackend()
    monkeypatch.setattr("drugpaper.detection.api.YoloBackend", lambda path, device: backend)
    app = create_app(settings(yolo_service_token="test-token"))
    async with app.router.lifespan_context(app):
        assert backend.calls == 1 and not backend.closed
    assert backend.closed
