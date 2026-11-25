"""`*_layout.json` / `*_middle.json` 的宽松 Pydantic 模型（设计文档 2.2 节）。

已知字段显式声明；未知字段保留在 `model_extra` 中，由 IR Builder 写入 `source_ref.extra`。
"""

from __future__ import annotations

import copy
from typing import Any

import orjson
from pydantic import BaseModel, ConfigDict, Field, field_validator

PtBox = tuple[float, float, float, float]

_LOOSE = ConfigDict(extra="allow")


def _to_bbox(v: Any) -> PtBox:
    if isinstance(v, list | tuple) and len(v) == 4:
        return (float(v[0]), float(v[1]), float(v[2]), float(v[3]))
    raise ValueError(f"bbox 必须是 4 个数：{v!r}")


class SubBlock(BaseModel):
    model_config = _LOOSE
    type: str = ""
    bbox: PtBox | None = None
    text: str | None = None
    page_idx: int | None = None
    parent_type: str | None = None

    @field_validator("bbox", mode="before")
    @classmethod
    def _bbox(cls, v: Any) -> PtBox | None:
        return None if v is None else _to_bbox(v)


class LayoutBlock(BaseModel):
    model_config = _LOOSE
    type: str = "unknown"
    bbox: PtBox = (0.0, 0.0, 0.0, 0.0)
    page_idx: int | None = None
    detailed_index: int | None = None
    confirmed_index: int | None = None
    text: str | None = None
    parent_type: str | None = None
    title_level: int | None = None
    caption_text: str | None = None
    footnote_text: str | None = None
    img_path: str | None = None
    merged_img_path: str | None = None
    sub_blocks: list[SubBlock] = Field(default_factory=list)
    merged_from: list[int] | None = None
    belong_to: int | None = None
    # 跨页合并（样例论文中没有出现，按字段名宽松接收）
    cross_page_merged: bool | None = None
    merged_from_cross: list[Any] | None = None
    belong_to_cross: Any | None = None
    cross_page_body_image_paths: list[str] | None = None

    @field_validator("bbox", mode="before")
    @classmethod
    def _bbox(cls, v: Any) -> PtBox:
        return _to_bbox(v)

    @field_validator("sub_blocks", mode="before")
    @classmethod
    def _subs(cls, v: Any) -> Any:
        return v or []

    @property
    def text_str(self) -> str:
        return (self.text or "").strip()

    def extra_fields(self) -> dict[str, Any]:
        return dict(self.model_extra or {})


class LayoutPage(BaseModel):
    model_config = _LOOSE
    page_idx: int
    page_size: tuple[float, float]
    para_blocks: list[LayoutBlock] = Field(default_factory=list)

    @field_validator("page_size", mode="before")
    @classmethod
    def _size(cls, v: Any) -> tuple[float, float]:
        return (float(v[0]), float(v[1]))


class MiddleSpan(BaseModel):
    model_config = _LOOSE
    bbox: PtBox | None = None
    content: str | None = None
    type: str | None = None
    cross_page: bool | None = None

    @field_validator("bbox", mode="before")
    @classmethod
    def _bbox(cls, v: Any) -> PtBox | None:
        return None if v is None else _to_bbox(v)


class MiddleLine(BaseModel):
    model_config = _LOOSE
    bbox: PtBox
    spans: list[MiddleSpan] = Field(default_factory=list)
    index: float | None = None  # middle.json 中可能是 4.5 这样的小数

    @field_validator("bbox", mode="before")
    @classmethod
    def _bbox(cls, v: Any) -> PtBox:
        return _to_bbox(v)

    @property
    def cross_page(self) -> bool:
        return any(s.cross_page for s in self.spans)

    @property
    def text(self) -> str:
        return "".join(s.content or "" for s in self.spans)


class MiddleBlock(BaseModel):
    model_config = _LOOSE
    type: str = ""
    bbox: PtBox = (0.0, 0.0, 0.0, 0.0)
    index: float | None = None  # middle.json 中可能是 4.5 这样的小数
    lines: list[MiddleLine] = Field(default_factory=list)
    lines_deleted: bool | None = None
    blocks: list[MiddleBlock] = Field(default_factory=list)

    @field_validator("bbox", mode="before")
    @classmethod
    def _bbox(cls, v: Any) -> PtBox:
        return _to_bbox(v)

    @field_validator("lines", "blocks", mode="before")
    @classmethod
    def _lists(cls, v: Any) -> Any:
        return v or []

    def all_lines(self) -> list[MiddleLine]:
        """自身 lines + 嵌套 blocks 的 lines（图片块的文本在子块中）。"""
        out = list(self.lines)
        for b in self.blocks:
            out.extend(b.all_lines())
        return out


class MiddlePage(BaseModel):
    model_config = _LOOSE
    page_idx: int
    page_size: tuple[float, float] | None = None
    para_blocks: list[MiddleBlock] = Field(default_factory=list)
    discarded_blocks: list[MiddleBlock] = Field(default_factory=list)

    @field_validator("para_blocks", "discarded_blocks", mode="before")
    @classmethod
    def _lists(cls, v: Any) -> Any:
        return v or []


class MiddleDoc(BaseModel):
    model_config = _LOOSE
    pdf_info: list[MiddlePage] = Field(default_factory=list)

    def page(self, page_idx: int) -> MiddlePage | None:
        for p in self.pdf_info:
            if p.page_idx == page_idx:
                return p
        return None


def load_layout(data: bytes | str | list[Any]) -> list[LayoutPage]:
    raw = orjson.loads(data) if isinstance(data, bytes | str) else data
    if not isinstance(raw, list):
        raise ValueError("layout.json 顶层应为页面数组")
    pages = [LayoutPage.model_validate(p) for p in raw]
    for p in pages:
        for b in p.para_blocks:
            if b.page_idx is None:
                b.page_idx = p.page_idx
    return pages


def load_middle(data: bytes | str | dict[str, Any]) -> MiddleDoc:
    raw = orjson.loads(data) if isinstance(data, bytes | str) else data
    if not isinstance(raw, dict) or "pdf_info" not in raw:
        raise ValueError("middle.json 缺少 pdf_info")
    raw = copy.deepcopy(raw)
    repaired: list[str] = []

    def clean(items: list[Any], path: str) -> list[Any]:
        result = []
        for i, item in enumerate(items):
            if not isinstance(item, dict):
                result.append(item)
                continue
            item_path = f"{path}[{i}]"
            for key in ("blocks", "lines", "spans"):
                if isinstance(item.get(key), list):
                    item[key] = clean(item[key], item_path + "." + key)
            if item.get("bbox") == []:
                boxes = [
                    child["bbox"]
                    for key in ("blocks", "lines", "spans")
                    for child in item.get(key, [])
                    if isinstance(child.get("bbox"), list) and len(child["bbox"]) == 4
                ]
                repaired.append(item_path)
                if boxes:
                    item["bbox"] = [
                        min(b[0] for b in boxes),
                        min(b[1] for b in boxes),
                        max(b[2] for b in boxes),
                        max(b[3] for b in boxes),
                    ]
                elif path.endswith("spans"):
                    item["bbox"] = None
                else:
                    continue
            result.append(item)
        return result

    for i, page in enumerate(raw["pdf_info"]):
        for key in ("para_blocks", "discarded_blocks"):
            page[key] = clean(page.get(key) or [], f"pdf_info[{i}].{key}")
    raw["repaired_geometry_paths"] = repaired
    return MiddleDoc.model_validate(raw)
