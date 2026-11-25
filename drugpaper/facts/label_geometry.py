from __future__ import annotations

import re
from typing import Any

from drugpaper.domain.geometry import BBox
from drugpaper.facts.labels import LABEL_PATTERN, normalize


def candidates(box: BBox, spans: list[dict[str, Any]], page: int) -> list[dict[str, Any]]:
    result = []
    for span in spans:
        if span.get("page") != page:
            continue
        label = normalize(span.get("text", ""))
        if not re.fullmatch(LABEL_PATTERN, label):
            continue
        region = BBox.model_validate(span["bbox"])
        x, y = region.center()
        below = (
            box.x0 - box.width / 2 <= x <= box.x1 + box.width / 2 and box.y1 <= y <= box.y1 + 1.5 * box.height
        )
        right = box.x1 <= x <= box.x1 + 1.5 * box.height and box.y0 <= y <= box.y1 + box.height / 2
        if below or right:
            distance = min(abs(y - box.y1), abs(x - box.x1))
            result.append(
                {
                    "label": label,
                    "score": max(0, 1 - distance / max(box.height * 1.5, 1e-8)),
                    "source_ref": span.get("ref"),
                    "bbox": span["bbox"],
                }
            )
    return sorted(result, key=lambda item: (-item["score"], item["label"]))
