from __future__ import annotations

import asyncio
import hashlib
import io
import json
import math
import time
from collections import OrderedDict, deque
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Literal, Protocol

from PIL import Image, UnidentifiedImageError

from drugpaper.config.settings import Settings
from drugpaper.detection.backend import DetectorBackend
from drugpaper.detection.models import Detection, DetectionError, DetectionReport

Preset = Literal["default", "high_recall"]
POSTPROCESS_VERSION = "1"


class DetectionService(Protocol):
    async def detect(self, user_id: str, data: bytes, preset: Preset = "default") -> DetectionReport: ...
    async def aclose(self) -> None: ...


@dataclass
class Job:
    user: str
    data: bytes
    preset: Preset
    future: asyncio.Future[DetectionReport]
    cancelled: bool = False
    enqueued: float = 0


class MoleculeDetectionService:
    """单推理线程 + 用户轮转有界队列。调用取消不会释放正在推理的槽位。"""

    def __init__(self, backend: DetectorBackend, settings: Settings) -> None:
        self.backend, self.settings = backend, settings
        self._queues: dict[str, deque[Job]] = {}
        self._ready: deque[str] = deque()
        self._counts: dict[str, int] = {}
        self._pending = 0
        self._wake = asyncio.Event()
        self._worker: asyncio.Task[None] | None = None
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="molecule-detect")
        self._closed = False
        self._close_lock = asyncio.Lock()
        self._shutdown_complete = False
        self._cache: OrderedDict[str, DetectionReport] = OrderedDict()

    async def detect(self, user_id: str, data: bytes, preset: Preset = "default") -> DetectionReport:
        if self._closed:
            raise DetectionError("E_BACKEND_UNAVAILABLE", "检测服务已关闭", retryable=True)
        if not user_id or preset not in ("default", "high_recall"):
            raise DetectionError("E_INVALID_INPUT", "用户或 preset 无效")
        if len(data) > self.settings.yolo_max_image_bytes:
            raise DetectionError("E_INVALID_IMAGE", "图片字节数超过上限")
        if self._counts.get(user_id, 0) >= self.settings.yolo_max_jobs_per_user:
            raise DetectionError("E_USER_BUSY", "当前用户的检测任务已达上限", retryable=True)
        if self._pending >= self.settings.yolo_queue_size:
            raise DetectionError("E_QUEUE_FULL", "检测队列已满，请稍后重试", retryable=True)
        future: asyncio.Future[DetectionReport] = asyncio.get_running_loop().create_future()
        job = Job(user_id, data, preset, future, enqueued=time.monotonic())
        if user_id not in self._queues:
            self._queues[user_id] = deque()
            self._ready.append(user_id)
        self._queues[user_id].append(job)
        self._counts[user_id] = self._counts.get(user_id, 0) + 1
        self._pending += 1
        if self._worker is None:
            self._worker = asyncio.create_task(self._run())
        self._wake.set()
        try:
            return await asyncio.wait_for(asyncio.shield(future), self.settings.yolo_timeout_s)
        except (TimeoutError, asyncio.CancelledError) as exc:
            job.cancelled = True
            future.cancel()
            if isinstance(exc, asyncio.CancelledError):
                raise
            raise DetectionError("E_TIMEOUT", "检测等待或执行超时", retryable=True) from exc

    async def _run(self) -> None:
        while True:
            await self._wake.wait()
            while self._ready:
                user = self._ready.popleft()
                jobs = self._queues[user]
                job = jobs.popleft()
                if jobs:
                    self._ready.append(user)
                else:
                    del self._queues[user]
                self._pending -= 1
                try:
                    if not job.cancelled:
                        report = await asyncio.get_running_loop().run_in_executor(
                            self._executor, self._infer, job
                        )
                        if not job.future.done():
                            job.future.set_result(report)
                except Exception as exc:
                    error = (
                        exc
                        if isinstance(exc, DetectionError)
                        else DetectionError("E_BACKEND_UNAVAILABLE", "检测后端执行失败", retryable=True)
                    )
                    if "out of memory" in str(exc).lower():
                        error = DetectionError("E_OOM", "检测设备内存不足", retryable=True)
                    if not job.future.done():
                        job.future.set_exception(error)
                finally:
                    self._counts[user] -= 1
                    if not self._counts[user]:
                        del self._counts[user]
            self._wake.clear()
            if self._closed:
                return

    def _infer(self, job: Job) -> DetectionReport:
        start = time.monotonic()
        if start - job.enqueued > self.settings.yolo_queue_timeout_s:
            raise DetectionError("E_TIMEOUT", "排队时间超过上限", retryable=True)
        try:
            with Image.open(io.BytesIO(job.data)) as source:
                if source.width * source.height > self.settings.yolo_max_pixels:
                    raise DetectionError("E_INVALID_IMAGE", "图片像素数超过上限")
                image = source.convert("RGB")
        except (
            OSError,
            UnidentifiedImageError,
            Image.DecompressionBombError,
            Image.DecompressionBombWarning,
        ) as exc:
            raise DetectionError("E_INVALID_IMAGE", "无法解码图片") from exc
        signature = self.backend.signature()
        conf = self.settings.yolo_high_recall_conf if job.preset == "high_recall" else self.settings.yolo_conf
        parameters = {
            "conf": conf,
            "iou": self.settings.yolo_iou,
            "imgsz": self.settings.yolo_imgsz,
            "max_det": self.settings.yolo_max_detections,
        }
        fingerprint = {
            "user": job.user,
            "image": hashlib.sha256(job.data).hexdigest(),
            "signature": signature,
            "parameters": parameters,
            "padding": self.settings.yolo_padding_ratio,
            "postprocess": POSTPROCESS_VERSION,
        }
        key = hashlib.sha256(json.dumps(fingerprint, sort_keys=True).encode()).hexdigest()
        if key in self._cache:
            self._cache.move_to_end(key)
            return self._cache[key].model_copy(deep=True, update={"cached": True})
        raw = self.backend.predict(
            image,
            conf=conf,
            iou=self.settings.yolo_iou,
            imgsz=self.settings.yolo_imgsz,
            max_det=self.settings.yolo_max_detections,
        )
        if len(raw) > self.settings.yolo_max_detections:
            raise DetectionError("E_RESULT_COUNT_MISMATCH", "检测框数量超过配置上限")
        items = []
        width, height = image.size
        for box in raw:
            x0, y0, x1, y1 = box.bbox
            if (
                not all(math.isfinite(n) for n in (*box.bbox, box.confidence))
                or not 0 <= box.confidence <= 1
                or box.class_id < 0
            ):
                raise DetectionError("E_INVALID_BOX", "检测返回非法数值")
            x0, x1 = max(0.0, x0), min(float(width), x1)
            y0, y1 = max(0.0, y0), min(float(height), y1)
            if x0 >= x1 or y0 >= y1:
                raise DetectionError("E_INVALID_BOX", "检测返回空框或越界框")
            if box.confidence < conf:
                continue
            norm = (x0 / width, y0 / height, x1 / width, y1 / height)
            pad = self.settings.yolo_padding_ratio
            dx, dy = (norm[2] - norm[0]) * pad, (norm[3] - norm[1]) * pad
            crop = (
                max(0.0, norm[0] - dx),
                max(0.0, norm[1] - dy),
                min(1.0, norm[2] + dx),
                min(1.0, norm[3] + dy),
            )
            items.append((norm, crop, box))
        items.sort(
            key=lambda item: (
                item[0][1],
                item[0][0],
                item[0],
                item[2].class_id,
                item[2].confidence,
                item[2].class_name,
            )
        )
        detections = [
            Detection(
                detection_id=f"det_{key[:24]}_{i:04d}",
                label_id=i,
                bbox_norm_xyxy=norm,
                crop_bbox_norm_xyxy=crop,
                confidence=box.confidence,
                class_id=box.class_id,
                class_name=box.class_name,
            )
            for i, (norm, crop, box) in enumerate(items, 1)
        ]
        report = DetectionReport(
            status="completed" if detections else "no_detections",
            result_key=key,
            width=width,
            height=height,
            detections=detections,
            provenance={
                **signature,
                **parameters,
                "image_sha256": fingerprint["image"],
                "padding_ratio": self.settings.yolo_padding_ratio,
                "postprocess_version": POSTPROCESS_VERSION,
                "queue_ms": int((start - job.enqueued) * 1000),
                "inference_ms": int((time.monotonic() - start) * 1000),
            },
        )
        if len(raw) == self.settings.yolo_max_detections:
            report.warnings.append("检测数量达到 max_detections，可能存在未返回的框")
        if not job.cancelled and self.settings.yolo_cache_size:
            self._cache[key] = report.model_copy(deep=True)
            while len(self._cache) > self.settings.yolo_cache_size:
                self._cache.popitem(last=False)
        return report

    async def aclose(self) -> None:
        async with self._close_lock:
            if self._shutdown_complete:
                return
            await self._shutdown()
            self._shutdown_complete = True

    async def _shutdown(self) -> None:
        self._closed = True
        for jobs in self._queues.values():
            for job in jobs:
                job.cancelled = True
                if not job.future.done():
                    job.future.set_exception(
                        DetectionError("E_BACKEND_UNAVAILABLE", "检测服务正在关闭", retryable=True)
                    )
        self._wake.set()
        if self._worker is not None:
            await self._worker
        await asyncio.get_running_loop().run_in_executor(self._executor, self.backend.close)
        self._executor.shutdown(wait=True)
        self._cache.clear()
