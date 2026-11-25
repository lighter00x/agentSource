from __future__ import annotations

import asyncio
import base64

import httpx
import httpx2
import pytest

from drugpaper.detection.api import create_app
from drugpaper.detection.models import DetectionError
from drugpaper.detection.remote import RemoteDetectionService
from drugpaper.detection.service import MoleculeDetectionService
from tests.unit.detection.test_service import FakeBackend, png, settings


async def test_two_application_clients_share_worker_queue():
    cfg = settings(yolo_base_url="http://worker", yolo_service_token="test-secret")
    backend = FakeBackend(delay=0.02)
    worker = MoleculeDetectionService(backend, cfg)
    app = create_app(cfg, worker)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://worker") as web:

        async def forward(request):
            response = await web.post("/detect", content=request.content, headers=dict(request.headers))
            return httpx2.Response(response.status_code, json=response.json())

        remotes = [
            RemoteDetectionService(cfg, client=httpx2.AsyncClient(transport=httpx2.MockTransport(forward)))
            for _ in range(2)
        ]
        try:
            results = await asyncio.gather(*(remotes[i % 2].detect(f"user{i}", png()) for i in range(6)))
            assert len(results) == 6 and backend.maximum == 1 and backend.calls == 6
            cfg.yolo_max_jobs_per_user = 1
            first = asyncio.create_task(remotes[0].detect("same", png("red")))
            await asyncio.sleep(0.005)
            with pytest.raises(DetectionError) as error:
                await remotes[1].detect("same", png("blue"))
            assert error.value.code == "E_USER_BUSY"
            await first
        finally:
            for remote in remotes:
                await remote.aclose()
            await worker.aclose()


async def test_worker_rejects_unauthorized_invalid_and_large_requests():
    cfg = settings(yolo_service_token="test-secret", yolo_max_image_bytes=1000)
    worker = MoleculeDetectionService(FakeBackend(), cfg)
    app = create_app(cfg, worker)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://worker") as web:
        payload = {"user_id": "a", "image_b64": base64.b64encode(png()).decode()}
        assert (await web.post("/detect", json=payload)).status_code == 403
        headers = {"Authorization": "Bearer test-secret"}
        response = await web.post("/detect", json=payload | {"image_b64": "!"}, headers=headers)
        assert response.status_code == 400
        assert response.json()["error"]["code"] == "E_INVALID_IMAGE"
        assert (await web.post("/detect", content=b"x" * 10000, headers=headers)).status_code == 413
    await worker.aclose()


async def test_remote_is_not_silent_fallback():
    cfg = settings(yolo_backend="remote", yolo_base_url="", yolo_service_token="")
    remote = RemoteDetectionService(cfg)
    try:
        with pytest.raises(DetectionError) as error:
            await remote.detect("a", png())
        assert error.value.code == "E_MODEL_NOT_READY"
    finally:
        await remote.aclose()
