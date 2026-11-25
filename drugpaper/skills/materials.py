"""版本固定的任务材料；由调用方提供或从 Document IR 装配。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from drugpaper.gateway.base import ImagePart


@dataclass
class Materials:
    target: dict[str, Any] = field(default_factory=dict)
    images: list[ImagePart] = field(default_factory=list)
    upstream_versions: dict[str, int] = field(default_factory=dict)
    node_hash: str = ""

    def variables(self, inputs: dict[str, Any]) -> dict[str, Any]:
        return {"inputs": inputs, "target": self.target, **inputs}
