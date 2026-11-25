from __future__ import annotations

import asyncio
import io
import threading
import time

import pytest
from PIL import Image

from drugpaper.config.settings import Settings
from drugpaper.detection.models import DetectionError, RawDetection
from drugpaper.detection.service import MoleculeDetectionService


def png(color="white", size=(100, 100)):
    out = io.BytesIO()
    Image.new("RGB", size, color).save(out, "PNG")
    return out.getvalue()


class FakeBackend:
    def __init__(self, boxes=None, delay=0):
        self.boxes = (
            boxes
            if boxes is not None
            else [RawDetection(bbox=(10, 20, 40, 60), confidence=0.9, class_id=0, class_name="molecule")]
        )
        self.delay = delay
        self.calls = 0
        self.active = 0
        self.maximum = 0
        self.closed = False
        self.started = threading.Event()
        self.colors = []

    def signature(self):
        return {"weights_sha256": "fake", "backend": "fake", "backend_version": "1"}

    def predict(self, image, **kwargs):
        self.calls += 1
        self.active += 1
        self.maximum = max(self.maximum, self.active)
        self.colors.append(image.getpixel((0, 0)))
        self.started.set()
        time.sleep(self.delay)
        self.active -= 1
        return self.boxes

    def close(self):
        assert self.active == 0
        self.closed = True


def settings(**kwargs):
    return Settings(_env_file=None, yolo_queue_size=16, yolo_max_jobs_per_user=8, **kwargs)


async def started(backend):
    for _ in range(100):
        if backend.started.is_set():
            return
        await asyncio.sleep(0.005)
    raise AssertionError("backend 未启动")


async def test_many_users_serial_inference_and_isolated_cache():
    backend = FakeBackend(delay=0.005)
    service = MoleculeDetectionService(backend, settings())
    try:
        results = await asyncio.gather(*(service.detect(f"user{i}", png()) for i in range(8)))
        assert backend.calls == 8 and backend.maximum == 1
        assert len({r.result_key for r in results}) == 8
        hit = await service.detect("user0", png())
        assert hit.cached and hit.detections == results[0].detections
        hit.detections.clear()
        assert len((await service.detect("user0", png())).detections) == 1
        changed = await service.detect("user0", png(), "high_recall")
        assert not changed.cached and changed.result_key != results[0].result_key
    finally:
        await service.aclose()
    assert backend.closed


async def test_round_robin_prevents_one_user_draining_queue():
    backend = FakeBackend(delay=0.03)
    service = MoleculeDetectionService(backend, settings())
    first = asyncio.create_task(service.detect("a", png("black")))
    await started(backend)
    pending = [asyncio.create_task(service.detect("a", png(color))) for color in ("red", "green", "blue")]
    pending.append(asyncio.create_task(service.detect("b", png("yellow"))))
    try:
        await asyncio.gather(first, *pending)
        assert backend.colors.index((255, 255, 0)) < backend.colors.index((0, 0, 255))
    finally:
        await service.aclose()


async def test_timeout_retains_running_slot_and_user_quota():
    cfg = settings(yolo_timeout_s=0.02)
    cfg.yolo_max_jobs_per_user = 1
    backend = FakeBackend(delay=0.1)
    service = MoleculeDetectionService(backend, cfg)
    try:
        with pytest.raises(DetectionError, match="超时") as error:
            await service.detect("a", png())
        assert error.value.code == "E_TIMEOUT"
        with pytest.raises(DetectionError) as error:
            await service.detect("a", png())
        assert error.value.code == "E_USER_BUSY"
        cfg.yolo_timeout_s = 0.4
        result = await service.detect("b", png())
        assert result.status == "completed" and backend.maximum == 1
        assert backend.calls == 2
    finally:
        await service.aclose()


async def test_queue_full_and_cancelled_job_is_not_inferred():
    cfg = settings()
    cfg.yolo_queue_size = 1
    backend = FakeBackend(delay=0.08)
    service = MoleculeDetectionService(backend, cfg)
    first = asyncio.create_task(service.detect("a", png()))
    await started(backend)
    queued = asyncio.create_task(service.detect("b", png()))
    await asyncio.sleep(0.01)
    try:
        with pytest.raises(DetectionError) as error:
            await service.detect("c", png())
        assert error.value.code == "E_QUEUE_FULL"
        queued.cancel()
        with pytest.raises(asyncio.CancelledError):
            await queued
        await first
        await service.aclose()
        assert backend.calls == 1
    finally:
        await service.aclose()


@pytest.mark.parametrize("box", [(0, 0, 0, 1), (101, 0, 120, 10), (float("nan"), 0, 10, 10)])
async def test_invalid_boxes_rejected(box):
    backend = FakeBackend([RawDetection(bbox=box, confidence=0.9, class_id=0, class_name="molecule")])
    service = MoleculeDetectionService(backend, settings())
    try:
        with pytest.raises(DetectionError) as error:
            await service.detect("a", png())
        assert error.value.code == "E_INVALID_BOX"
    finally:
        await service.aclose()


async def test_overlap_keeps_multiple_boxes_and_padding_by_width():
    boxes = [
        RawDetection(bbox=(0, 0, 90, 100), confidence=0.9, class_id=0, class_name="molecule"),
        RawDetection(bbox=(10, 0, 100, 100), confidence=0.8, class_id=0, class_name="molecule"),
    ]
    service = MoleculeDetectionService(FakeBackend(boxes), settings())
    try:
        result = await service.detect("a", png())
        assert len(result.detections) == 2
        assert result.detections[1].bbox_norm_xyxy == (0.1, 0, 1, 1)
        assert result.detections[1].crop_bbox_norm_xyxy[0] == pytest.approx(0.055)
    finally:
        await service.aclose()


@pytest.mark.parametrize(
    "data,options", [(b"bad", {}), (png(), {"yolo_max_image_bytes": 1}), (png(), {"yolo_max_pixels": 1})]
)
async def test_image_limits(data, options):
    service = MoleculeDetectionService(FakeBackend(), settings(**options))
    try:
        with pytest.raises(DetectionError) as error:
            await service.detect("a", data)
        assert error.value.code == "E_INVALID_IMAGE"
    finally:
        await service.aclose()


async def test_empty_detection_and_bounded_cache():
    service = MoleculeDetectionService(FakeBackend([]), settings(yolo_cache_size=1))
    try:
        first = await service.detect("a", png())
        assert first.status == "no_detections"
        await service.detect("b", png())
        assert not (await service.detect("a", png())).cached
    finally:
        await service.aclose()
    with pytest.raises(DetectionError) as error:
        await service.detect("a", png())
    assert error.value.code == "E_BACKEND_UNAVAILABLE"


async def test_queue_deadline_and_graceful_close_skip_waiting_jobs():
    cfg = settings(yolo_queue_timeout_s=0.01)
    backend = FakeBackend(delay=0.05)
    service = MoleculeDetectionService(backend, cfg)
    first = asyncio.create_task(service.detect("a", png()))
    await started(backend)
    queued = asyncio.create_task(service.detect("b", png()))
    try:
        await first
        with pytest.raises(DetectionError) as error:
            await queued
        assert error.value.code == "E_TIMEOUT" and backend.calls == 1
        backend.started.clear()
        active = asyncio.create_task(service.detect("a", png("blue")))
        await started(backend)
        queued = asyncio.create_task(service.detect("b", png("red")))
        await asyncio.sleep(0.005)
        await service.aclose()
        await active
        with pytest.raises(DetectionError) as error:
            await queued
        assert error.value.code == "E_BACKEND_UNAVAILABLE"
        assert backend.active == 0 and backend.closed
    finally:
        await service.aclose()


async def test_backend_failure_is_typed_and_worker_recovers():
    class FailingBackend(FakeBackend):
        def predict(self, image, **kwargs):
            if not self.calls:
                self.calls += 1
                raise RuntimeError("CUDA out of memory")
            return super().predict(image, **kwargs)

    backend = FailingBackend()
    service = MoleculeDetectionService(backend, settings())
    try:
        with pytest.raises(DetectionError) as error:
            await service.detect("a", png())
        assert error.value.code == "E_OOM" and error.value.retryable
        assert (await service.detect("b", png())).status == "completed"
    finally:
        await service.aclose()
