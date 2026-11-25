"""文档材料检测与按用户隔离的结果读取、裁剪、编号标注。"""

from __future__ import annotations

import asyncio
import io
import json
import math
from typing import TYPE_CHECKING, Any

from PIL import Image, ImageDraw, ImageFont
from pydantic import BaseModel, Field

from drugpaper.detection.models import DetectionError, DetectionReport
from drugpaper.detection.service import Preset
from drugpaper.docsrc.ir_source import IrDocumentSource
from drugpaper.domain.ids import parse_ref
from drugpaper.persistence.models import Document
from drugpaper.storage.base import BlobNotFound
from drugpaper.tools.protocol import Tool, ToolContext, ToolResult, tool

if TYPE_CHECKING:
    from drugpaper.services import Services


class DetectIn(BaseModel):
    target_ref: str
    parent_region_index: int = Field(default=0, ge=0)
    preset: Preset = "default"


class ResultIn(BaseModel):
    result_ref: str = Field(pattern=r"^[0-9a-f]{64}$")
    offset: int = Field(default=0, ge=0)
    limit: int = Field(default=10, ge=1, le=10)


class CropIn(BaseModel):
    result_ref: str = Field(pattern=r"^[0-9a-f]{64}$")
    label_ids: list[int] = Field(min_length=1, max_length=10)


class RenderIn(BaseModel):
    result_ref: str = Field(pattern=r"^[0-9a-f]{64}$")


async def check_document(sv: Services, ctx: ToolContext, target_ref: str) -> tuple[str, int, str]:
    parsed = parse_ref(target_ref)
    if not parsed:
        raise DetectionError("E_INVALID_TARGET", "必须提供完整图像节点引用")
    document_id, revision, local_id = parsed
    if ctx.document_id and ctx.document_id != document_id:
        raise DetectionError("E_FORBIDDEN", "不能访问其他文档")
    async with sv.db.session(ctx.user_id) as session:
        document = await session.get(Document, document_id)
        if document is None or document.user_id != ctx.user_id:
            raise DetectionError("E_FORBIDDEN", "文档不存在或对当前用户不可见")
        sha = document.sha256
    return sha, revision, local_id


def _png(image: Image.Image) -> bytes:
    file = io.BytesIO()
    image.save(file, format="PNG")
    return file.getvalue()


def _crop(data: bytes, box: tuple[float, float, float, float]) -> bytes:
    with Image.open(io.BytesIO(data)) as source:
        width, height = source.size
        pixels = (
            math.floor(box[0] * width),
            math.floor(box[1] * height),
            math.ceil(box[2] * width),
            math.ceil(box[3] * height),
        )
        return _png(source.convert("RGB").crop(pixels))


def _annotate(data: bytes, report: DetectionReport) -> bytes:
    with Image.open(io.BytesIO(data)) as source:
        image = source.convert("RGB")
    draw = ImageDraw.Draw(image)
    font = ImageFont.load_default(size=max(16, min(64, max(image.size) // 40)))
    for detection in report.detections:
        x0, y0, x1, y1 = detection.bbox_norm_xyxy
        box = (x0 * image.width, y0 * image.height, x1 * image.width, y1 * image.height)
        draw.rectangle(box, outline="red", width=2)
        text = str(detection.label_id)
        tx0, ty0, tx1, ty1 = draw.textbbox((0, 0), text, font=font)
        label_w, label_h = tx1 - tx0 + 6, ty1 - ty0 + 6
        x = max(0, min(box[2] + 3, image.width - label_w))
        y = max(0, box[1] - label_h)
        draw.rectangle((x, y, min(image.width, x + label_w), min(image.height, y + label_h)), fill="white")
        draw.text((x + 3 - tx0, y + 3 - ty0), text, fill="blue", font=font)
    return _png(image)


def make_detection_tools(sv: Services) -> list[Tool]:
    async def read(ctx: ToolContext, ref: str) -> tuple[dict[str, Any], DetectionReport]:
        try:
            data = json.loads(await sv.store_for(ctx.user_id).get(ref))
        except (BlobNotFound, ValueError) as exc:
            raise DetectionError("E_RESULT_NOT_FOUND", "检测结果不存在或不可见") from exc
        if (
            not isinstance(data, dict)
            or data.get("user_id") != ctx.user_id
            or data.get("kind") != "molecule_detection_v1"
        ):
            raise DetectionError("E_FORBIDDEN", "检测结果对当前用户不可见")
        await check_document(sv, ctx, data["target_ref"])
        return data, DetectionReport.model_validate(data["report"])

    def failure(exc: DetectionError) -> ToolResult:
        return ToolResult(ok=False, error=exc.code, data={"message": str(exc), "retryable": exc.retryable})

    @tool(
        "molecule.detect",
        "底层候选框检测，只返回结果引用，不建立IR分子节点；完整识别/回填流程先用molecule_detect Skill。",
        side_effect="external_model",
        timeout_s=sv.settings.yolo_timeout_s + 30,
    )
    async def detect(ctx: ToolContext, args: DetectIn) -> ToolResult:
        try:
            sha, revision, _ = await check_document(sv, ctx, args.target_ref)
            source = await IrDocumentSource.load(sv.db, parse_ref(args.target_ref)[0], revision)  # type: ignore[index]
            node = source.node(args.target_ref)
            if node.node_type not in ("figure", "table", "scheme", "figure_part", "molecule", "chart_panel"):
                raise DetectionError("E_INVALID_TARGET", "该节点不是可检测的图像类型")
            if args.parent_region_index >= len(node.geometry.regions):
                raise DetectionError("E_INVALID_TARGET", "parent_region_index 越界")
            region = node.geometry.regions[args.parent_region_index]
            store = sv.store_for(ctx.user_id)
            image_ref = await sv.renderer_for(ctx.user_id).crop(sha, region, dpi=sv.settings.crop_dpi, pad=0)
            report = await sv.detector().detect(ctx.user_id, await store.get(image_ref), args.preset)
            report_data = report.model_dump(mode="json")
            # cached 是调用状态，不写入不可变结果资产。
            report_data["cached"] = False
            report_data["provenance"].pop("queue_ms", None)
            report_data["provenance"].pop("inference_ms", None)
            result = {
                "kind": "molecule_detection_v1",
                "user_id": ctx.user_id,
                "target_ref": args.target_ref,
                "parent_region_index": args.parent_region_index,
                "parent_region": region.model_dump(mode="json"),
                "image_ref": image_ref,
                "report": report_data,
            }
            result_ref = await store.put(json.dumps(result, sort_keys=True).encode(), "application/json")
            return ToolResult(
                ok=True,
                refs=[args.target_ref],
                data={
                    "status": report.status,
                    "result_ref": result_ref,
                    "image_ref": image_ref,
                    "count": len(report.detections),
                    "cached": report.cached,
                    "warnings": report.warnings,
                    "provenance": report.provenance,
                },
            )
        except DetectionError as exc:
            return failure(exc)

    @tool(
        "molecule.detections",
        "分页读取检测结果，每次最多 10 个框，保留编号及置信度。",
        max_output_chars=12000,
    )
    async def results(ctx: ToolContext, args: ResultIn) -> ToolResult:
        try:
            data, report = await read(ctx, args.result_ref)
            items = report.detections[args.offset : args.offset + args.limit]
            end = args.offset + len(items)
            return ToolResult(
                ok=True,
                refs=[data["target_ref"]],
                data={
                    "target_ref": data["target_ref"],
                    "parent_region_index": data["parent_region_index"],
                    "width": report.width,
                    "height": report.height,
                    "detections": [item.model_dump(mode="json") for item in items],
                    "total": len(report.detections),
                    "next_offset": end if end < len(report.detections) else None,
                },
            )
        except DetectionError as exc:
            return failure(exc)

    @tool("molecule.crop", "按检测结果中的 label_ids 裁剪最多 10 个分子，返回编号和图片引用。")
    async def crop(ctx: ToolContext, args: CropIn) -> ToolResult:
        try:
            data, report = await read(ctx, args.result_ref)
            by_id = {item.label_id: item for item in report.detections}
            if len(set(args.label_ids)) != len(args.label_ids) or any(
                label not in by_id for label in args.label_ids
            ):
                raise DetectionError("E_INVALID_LABEL", "编号不存在或重复")
            store = sv.store_for(ctx.user_id)
            image = await store.get(data["image_ref"])
            crops = []
            for label in args.label_ids:
                item = by_id[label]
                blob = await store.put(
                    await asyncio.to_thread(_crop, image, item.crop_bbox_norm_xyxy), "image/png"
                )
                crops.append(
                    {
                        "label_id": label,
                        "detection_id": item.detection_id,
                        "image_ref": blob,
                        "crop_bbox_norm_xyxy": item.crop_bbox_norm_xyxy,
                    }
                )
            return ToolResult(ok=True, refs=[data["target_ref"]], data={"crops": crops})
        except DetectionError as exc:
            return failure(exc)

    @tool("som.render", "绘制检测编号标注图，编号与检测结果及裁剪结果保持一致。")
    async def render(ctx: ToolContext, args: RenderIn) -> ToolResult:
        try:
            data, report = await read(ctx, args.result_ref)
            store = sv.store_for(ctx.user_id)
            blob = await store.put(
                await asyncio.to_thread(_annotate, await store.get(data["image_ref"]), report), "image/png"
            )
            return ToolResult(
                ok=True,
                refs=[data["target_ref"]],
                data={"image_ref": blob, "count": len(report.detections), "result_ref": args.result_ref},
            )
        except DetectionError as exc:
            return failure(exc)

    return [detect, results, crop, render]
