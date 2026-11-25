from __future__ import annotations

import asyncio
import base64
import time

import httpx2
from pydantic import ValidationError

from drugpaper.config.settings import Settings
from drugpaper.detection.models import DetectionError, DetectionReport
from drugpaper.detection.service import Preset


class RemoteDetectionService:
    """多个应用进程共用检测 Worker；用户身份仅由可信应用上下文传入。"""

    def __init__(
        self, settings: Settings, *, client: httpx2.AsyncClient | None = None, wait_for_capacity: bool = False
    ) -> None:
        self.settings = settings
        self.wait_for_capacity = wait_for_capacity
        self.client = client or httpx2.AsyncClient(timeout=settings.yolo_timeout_s + 5, trust_env=False)

    async def detect(self, user_id: str, data: bytes, preset: Preset = "default") -> DetectionReport:
        deadline = time.monotonic() + self.settings.yolo_timeout_s
        while True:
            try:
                return await self._detect_once(user_id, data, preset)
            except DetectionError as exc:
                if (
                    not self.wait_for_capacity
                    or exc.code not in {"E_USER_BUSY", "E_QUEUE_FULL"}
                    or time.monotonic() >= deadline
                ):
                    raise
                await asyncio.sleep(0.2)

    async def _detect_once(self, user_id: str, data: bytes, preset: Preset = "default") -> DetectionReport:
        if not self.settings.yolo_base_url or not self.settings.yolo_service_token:
            raise DetectionError("E_MODEL_NOT_READY", "remote 模式需配置 YOLO_BASE_URL 与 YOLO_SERVICE_TOKEN")
        if len(data) > self.settings.yolo_max_image_bytes:
            raise DetectionError("E_INVALID_IMAGE", "图片字节数超过上限")
        try:
            response = await self.client.post(
                self.settings.yolo_base_url.rstrip("/") + "/detect",
                headers={"Authorization": "Bearer " + self.settings.yolo_service_token},
                json={"user_id": user_id, "image_b64": base64.b64encode(data).decode(), "preset": preset},
            )
        except httpx2.TimeoutException as exc:
            raise DetectionError("E_TIMEOUT", "远程检测超时", retryable=True) from exc
        except httpx2.TransportError as exc:
            raise DetectionError("E_BACKEND_UNAVAILABLE", "无法连接检测 Worker", retryable=True) from exc
        if response.status_code != 200:
            try:
                payload = response.json()
                error = payload.get("error", {}) if isinstance(payload, dict) else {}
                if not isinstance(error, dict):
                    error = {}
            except ValueError:
                error = {}
            raise DetectionError(
                str(error.get("code", "E_BACKEND_UNAVAILABLE")),
                str(error.get("message", "检测 Worker 拒绝请求")),
                retryable=bool(error.get("retryable", response.status_code >= 500)),
            )
        try:
            return DetectionReport.model_validate(response.json())
        except (ValueError, ValidationError) as exc:
            raise DetectionError("E_INVALID_RESPONSE", "检测 Worker 返回不合法结果") from exc

    async def aclose(self) -> None:
        await self.client.aclose()
