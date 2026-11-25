"""真实权重验收：独立样例、并发用户、缓存、标注与裁剪输出。"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from PIL import Image, ImageDraw  # noqa: E402

from drugpaper.config.settings import Settings  # noqa: E402
from drugpaper.detection.backend import YoloBackend  # noqa: E402
from drugpaper.detection.service import MoleculeDetectionService  # noqa: E402


class ObservedBackend(YoloBackend):
    def __init__(self, path, device):
        super().__init__(path, device)
        self.active = self.maximum = self.loads = 0

    def predict(self, image, **kwargs):
        self.active += 1
        self.maximum = max(self.maximum, self.active)
        before = self._model
        try:
            return super().predict(image, **kwargs)
        finally:
            if before is None and self._model is not None:
                self.loads += 1
            self.active -= 1


async def verify(args):
    out = args.out or ROOT / "samples/outputs/molecule-detection" / datetime.now(
        ZoneInfo("Asia/Shanghai")
    ).strftime("%Y%m%d-%H%M%S")
    out.mkdir(parents=True, exist_ok=True)
    cfg = Settings(
        _env_file=None,
        yolo_model_path=args.model,
        yolo_device=args.device,
        yolo_timeout_s=180,
        yolo_queue_timeout_s=120,
    )
    backend = ObservedBackend(args.model, args.device)
    service = MoleculeDetectionService(backend, cfg)
    images = [path.read_bytes() for path in args.images]
    try:
        reports = await asyncio.gather(
            *(service.detect(user, data) for user in ("user_a", "user_b") for data in images)
        )
        hit = await service.detect("user_a", images[0])
        assert hit.cached and hit.detections == reports[0].detections
        assert backend.maximum == 1 and backend.loads == 1
        assert reports[0].result_key != reports[len(images)].result_key
        for index, (path, report) in enumerate(zip(args.images, reports, strict=False)):
            image = Image.open(path).convert("RGB")
            draw = ImageDraw.Draw(image)
            for item in report.detections:
                x0, y0, x1, y1 = item.bbox_norm_xyxy
                draw.rectangle(
                    (x0 * image.width, y0 * image.height, x1 * image.width, y1 * image.height),
                    outline="red",
                    width=2,
                )
                draw.text((x0 * image.width, y0 * image.height), str(item.label_id), fill="blue")
            image.save(out / f"image_{index}_annotated.png")
            with Image.open(path) as source:
                for item in report.detections:
                    x0, y0, x1, y1 = item.crop_bbox_norm_xyxy
                    source.crop(
                        (x0 * source.width, y0 * source.height, x1 * source.width, y1 * source.height)
                    ).save(out / f"image_{index}_mol_{item.label_id}.png")
        record = {
            "model_loads": backend.loads,
            "max_active_inferences": backend.maximum,
            "cache_hit": hit.cached,
            "reports": [r.model_dump(mode="json") for r in reports],
        }
        (out / "report.json").write_text(json.dumps(record, ensure_ascii=False, indent=2))
        (out / "report.md").write_text(
            f"# 分子检测真实权重验收\n\n模型加载 {backend.loads} 次；多用户并发最大同时推理 {backend.maximum}；重复调用命中缓存。\n\n框数量：{[len(r.detections) for r in reports]}。框/置信度/类别及权重摘要见 report.json；标注与裁剪图保留供人工复核。本验收验证真实推理和封装行为，不等于检测精度专家签核。\n"
        )
        print(
            json.dumps(
                {
                    "out": str(out),
                    "counts": [len(r.detections) for r in reports],
                    "model_loads": backend.loads,
                    "max_active": backend.maximum,
                    "cached": hit.cached,
                },
                ensure_ascii=False,
            )
        )
    finally:
        await service.aclose()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--images", type=Path, nargs="+", required=True)
    parser.add_argument("--device", default="cpu")
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    if not 1 <= len(args.images) <= 2:
        parser.error("验收脚本接受 1～2 张图片，以符合每用户默认队列配额")
    import torch

    torch.set_num_threads(2)
    asyncio.run(verify(args))


if __name__ == "__main__":
    main()
