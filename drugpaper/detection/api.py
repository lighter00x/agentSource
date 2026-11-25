"""独立检测 Worker：uvicorn drugpaper.detection.api:create_app --factory --workers 1。"""

from __future__ import annotations

import base64
import binascii
import io
import secrets
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Header, Request
from fastapi.responses import JSONResponse
from PIL import Image
from pydantic import BaseModel, Field
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from drugpaper.config.settings import Settings, get_settings
from drugpaper.detection.backend import YoloBackend
from drugpaper.detection.models import DetectionError, DetectionReport
from drugpaper.detection.service import MoleculeDetectionService, Preset


class DetectIn(BaseModel):
    user_id: str = Field(min_length=1, max_length=128)
    image_b64: str = Field(min_length=1, max_length=32_000_000)
    preset: Preset = "default"


class BatchDetectIn(BaseModel):
    user_id: str = Field(min_length=1, max_length=128)
    images: list[str] = Field(min_length=1, max_length=32)
    preset: Preset = "default"


class RequestLimit:
    """在 JSON 解码前限制完整请求字节数，包含分块请求。"""

    def __init__(self, app: ASGIApp, maximum: int) -> None:
        self.app, self.maximum = app, maximum

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        messages = []
        size = 0
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            size += len(message.get("body", b""))
            if size > self.maximum:
                await JSONResponse(
                    status_code=413,
                    content={"error": {"code": "E_INVALID_IMAGE", "message": "请求过大", "retryable": False}},
                )(scope, receive, send)
                return
            messages.append(message)
            if not message.get("more_body", False):
                break

        async def replay() -> Message:
            if messages:
                return messages.pop(0)
            return await receive()

        await self.app(scope, replay, send)


def create_app(settings: Settings | None = None, service: MoleculeDetectionService | None = None) -> FastAPI:
    settings = settings or get_settings()
    detector = service or MoleculeDetectionService(
        YoloBackend(settings.yolo_model_path, settings.yolo_device), settings
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        if not settings.yolo_service_token:
            raise RuntimeError("检测 Worker 必须配置 YOLO_SERVICE_TOKEN")
        try:
            if service is None:
                warmup = io.BytesIO()
                Image.new("RGB", (640, 640), "white").save(warmup, "PNG")
                await detector.detect("__warmup__", warmup.getvalue())
            yield
        finally:
            await detector.aclose()

    app = FastAPI(title="Molecule detection worker", lifespan=lifespan)
    app.add_middleware(RequestLimit, maximum=settings.yolo_max_image_bytes * 4 // 3 + 4096)

    def authorized(authorization: str, service_token: str = "") -> bool:
        return bool(settings.yolo_service_token) and (
            secrets.compare_digest(authorization.encode(), ("Bearer " + settings.yolo_service_token).encode())
            or secrets.compare_digest(service_token.encode(), settings.yolo_service_token.encode())
        )

    @app.get("/health")
    async def health() -> dict[str, object]:
        return {
            "loaded": getattr(detector.backend, "_model", None) is not None,
            "device": settings.yolo_device,
            "queue_size": detector._pending,
            "queue_maxsize": settings.yolo_queue_size,
        }

    async def batch(user: str, images: list[bytes | None], preset: Preset) -> dict[str, object]:
        results = []
        for i, image in enumerate(images):
            try:
                if image is None:
                    raise DetectionError("E_INVALID_IMAGE", "无效 base64")
                report = await detector.detect(user, image, preset)
                results.append(
                    {
                        "input_index": i,
                        "width": report.width,
                        "height": report.height,
                        "boxes": [
                            {
                                "xyxy_px": [
                                    d.bbox_norm_xyxy[0] * report.width,
                                    d.bbox_norm_xyxy[1] * report.height,
                                    d.bbox_norm_xyxy[2] * report.width,
                                    d.bbox_norm_xyxy[3] * report.height,
                                ],
                                "xywh_norm": [
                                    (d.bbox_norm_xyxy[0] + d.bbox_norm_xyxy[2]) / 2,
                                    (d.bbox_norm_xyxy[1] + d.bbox_norm_xyxy[3]) / 2,
                                    d.bbox_norm_xyxy[2] - d.bbox_norm_xyxy[0],
                                    d.bbox_norm_xyxy[3] - d.bbox_norm_xyxy[1],
                                ],
                                "confidence": d.confidence,
                                "cls": d.class_id,
                            }
                            for d in report.detections
                        ],
                        "provenance": report.provenance,
                    }
                )
            except DetectionError as exc:
                results.append({"input_index": i, "width": 0, "height": 0, "boxes": [], "error": exc.code})
        return {"results": results}

    @app.post("/detect/batch", response_model=None)
    async def detect_batch(
        request: BatchDetectIn,
        authorization: str = Header(default=""),
        x_service_token: str = Header(default=""),
    ) -> dict[str, object] | JSONResponse:
        if not authorized(authorization, x_service_token):
            return JSONResponse(status_code=403, content={"error": "E_FORBIDDEN"})
        images: list[bytes | None] = []
        for value in request.images:
            try:
                images.append(base64.b64decode(value, validate=True))
            except binascii.Error:
                images.append(None)
        return await batch(request.user_id, images, request.preset)

    @app.post("/detect/files", response_model=None)
    async def detect_files(
        request: Request, authorization: str = Header(default=""), x_service_token: str = Header(default="")
    ) -> dict[str, object] | JSONResponse:
        if not authorized(authorization, x_service_token):
            return JSONResponse(status_code=403, content={"error": "E_FORBIDDEN"})
        async with request.form(max_files=32) as form:
            files = form.getlist("files")
            user = str(form.get("user_id", "default"))
            if not files or len(files) > 32 or not 1 <= len(user) <= 128:
                return JSONResponse(status_code=422, content={"error": "E_INVALID_INPUT"})
            from starlette.datastructures import UploadFile

            if not all(isinstance(file, UploadFile) for file in files):
                return JSONResponse(status_code=422, content={"error": "E_INVALID_INPUT"})
            return await batch(
                user, [await file.read() for file in files if isinstance(file, UploadFile)], "default"
            )

    @app.post("/detect", response_model=DetectionReport)
    async def detect(
        request: DetectIn, authorization: str = Header(default=""), x_service_token: str = Header(default="")
    ) -> DetectionReport | JSONResponse:
        if not authorized(authorization, x_service_token):
            return JSONResponse(
                status_code=403,
                content={"error": {"code": "E_FORBIDDEN", "message": "服务令牌无效", "retryable": False}},
            )
        try:
            data = base64.b64decode(request.image_b64, validate=True)
            return await detector.detect(request.user_id, data, request.preset)
        except binascii.Error:
            return JSONResponse(
                status_code=400,
                content={"error": {"code": "E_INVALID_IMAGE", "message": "无效 base64", "retryable": False}},
            )
        except DetectionError as exc:
            return JSONResponse(
                status_code=503 if exc.retryable else 400,
                content={"error": {"code": exc.code, "message": str(exc), "retryable": exc.retryable}},
            )

    return app
