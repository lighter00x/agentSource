from __future__ import annotations

import hashlib
import importlib
from pathlib import Path
from typing import Any, Protocol

from PIL import Image

from drugpaper.detection.models import DetectionError, RawDetection


class DetectorBackend(Protocol):
    def signature(self) -> dict[str, str]: ...
    def predict(
        self, image: Image.Image, *, conf: float, iou: float, imgsz: int, max_det: int
    ) -> list[RawDetection]: ...
    def close(self) -> None: ...


class YoloBackend:
    """构造、加载和推理均在服务的同一专用线程，实例永不并发共享。"""

    def __init__(self, path: Path | None, device: str) -> None:
        self.path, self.device = path, device
        self._model: Any = None
        self._signature: dict[str, str] | None = None
        self._stat: tuple[int, int] | None = None

    def signature(self) -> dict[str, str]:
        if self.path is None or not self.path.is_file():
            raise DetectionError("E_MODEL_NOT_READY", "未配置有效的 YOLO_MODEL_PATH")
        stat = self.path.stat()
        marker = (stat.st_mtime_ns, stat.st_size)
        if self._stat is not None and marker != self._stat:
            raise DetectionError("E_MODEL_CHANGED", "权重文件已变化，请重启检测 Worker")
        if self._signature is None:
            try:
                ultralytics = importlib.import_module("ultralytics")
            except ImportError as exc:
                raise DetectionError("E_MODEL_NOT_READY", "请安装项目 yolo 可选依赖") from exc
            with self.path.open("rb") as file:
                digest = hashlib.file_digest(file, "sha256").hexdigest()
            self._signature = {
                "weights_sha256": digest,
                "backend": "ultralytics",
                "backend_version": str(ultralytics.__version__),
                "device": self.device,
            }
            self._stat = marker
        return self._signature.copy()

    def predict(
        self, image: Image.Image, *, conf: float, iou: float, imgsz: int, max_det: int
    ) -> list[RawDetection]:
        self.signature()
        if self._model is None:
            self._model = importlib.import_module("ultralytics").YOLO(str(self.path), task="detect")
        results = self._model.predict(
            [image],
            conf=conf,
            iou=iou,
            imgsz=imgsz,
            max_det=max_det,
            device=self.device,
            agnostic_nms=True,
            verbose=False,
        )
        if len(results) != 1:
            raise DetectionError("E_RESULT_COUNT_MISMATCH", "检测结果数量与输入不一致")
        result = results[0]
        boxes = result.boxes
        if boxes is None:
            return []
        xyxy, scores, classes = boxes.xyxy.cpu().tolist(), boxes.conf.cpu().tolist(), boxes.cls.cpu().tolist()
        if not len(xyxy) == len(scores) == len(classes):
            raise DetectionError("E_RESULT_COUNT_MISMATCH", "框、置信度和类别数量不一致")
        return [
            RawDetection(
                bbox=box, confidence=score, class_id=int(cls), class_name=str(result.names[int(cls)])
            )
            for box, score, cls in zip(xyxy, scores, classes, strict=True)
        ]

    def close(self) -> None:
        self._model = None
