from __future__ import annotations

import math
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class DetectionError(Exception):
    def __init__(self, code: str, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.code, self.retryable = code, retryable


class RawDetection(BaseModel):
    model_config = ConfigDict(extra="forbid")
    bbox: tuple[float, float, float, float]
    confidence: float
    class_id: int
    class_name: str = Field(max_length=128)


class Detection(BaseModel):
    detection_id: str
    label_id: int
    bbox_norm_xyxy: tuple[float, float, float, float]
    crop_bbox_norm_xyxy: tuple[float, float, float, float]
    confidence: float = Field(ge=0, le=1)
    class_id: int
    class_name: str = Field(max_length=128)

    @field_validator("bbox_norm_xyxy", "crop_bbox_norm_xyxy")
    @classmethod
    def valid_box(cls, box: tuple[float, float, float, float]) -> tuple[float, float, float, float]:
        if not all(math.isfinite(n) for n in box) or not (
            0 <= box[0] < box[2] <= 1 and 0 <= box[1] < box[3] <= 1
        ):
            raise ValueError("非法归一化框")
        return box


class DetectionReport(BaseModel):
    status: Literal["completed", "no_detections"]
    result_key: str
    width: int = Field(gt=0)
    height: int = Field(gt=0)
    detections: list[Detection]
    provenance: dict[str, str | int | float | bool]
    warnings: list[str] = Field(default_factory=list)
    cached: bool = False
