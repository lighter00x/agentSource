"""文档节点与 Document IR（设计文档 6.4、6.9 节）。"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from drugpaper.domain.geometry import Geometry, Region
from drugpaper.domain.ids import global_ref

NodeType = Literal[
    "document",
    "front_matter",
    "section",
    "heading",
    "paragraph",
    "list",
    "figure",
    "scheme",
    "table",
    "chart_panel",
    "figure_part",
    "molecule",
    "caption",
    "footnote",
    "formula",
    "reference_entry",
    "discarded",
    "unknown",
]

VISUAL_TYPES: frozenset[str] = frozenset({"figure", "scheme", "table", "chart_panel"})
TEXT_TYPES: frozenset[str] = frozenset({"paragraph", "heading", "caption", "footnote", "list", "formula"})

RelationType = Literal["contains", "caption_of", "footnote_of", "continuation", "absorbed_from"]


class DocumentNode(BaseModel):
    document_id: str
    revision: int
    local_id: str
    node_type: NodeType
    subtype: str | None = None
    parent_id: str | None = None  # 父节点局部 ID
    children: list[str] = Field(default_factory=list)
    reading_order: int = 0
    geometry: Geometry = Field(default_factory=Geometry)
    label: str | None = None  # "Figure 2"、"Table 1"、"Scheme 3"
    text_raw: str | None = None
    text_norm: str | None = None
    section_role: str | None = None
    source: Literal["mineru", "skill", "human"] = "mineru"
    source_ref: dict[str, Any] = Field(default_factory=dict)
    quality_flags: list[str] = Field(default_factory=list)
    status: Literal["active", "removed", "needs_review"] = "active"
    attributes: dict[str, Any] = Field(default_factory=dict)

    @property
    def ref(self) -> str:
        return global_ref(self.document_id, self.revision, self.local_id)

    @property
    def page(self) -> int | None:
        return self.geometry.page

    def flag(self, name: str) -> None:
        if name not in self.quality_flags:
            self.quality_flags.append(name)


class Relation(BaseModel):
    source: str  # 局部 ID
    target: str
    type: RelationType
    evidence: dict[str, Any] = Field(default_factory=dict)
    status: Literal["active", "removed"] = "active"


class EvidenceRef(BaseModel):
    node_id: str  # 全局节点引用
    field_path: str | None = None  # 表格 "cells[3][5]"、Artifact 字段
    char_span: tuple[int, int] | None = None  # 段落内字符区间（text_raw 坐标）
    regions: list[Region] | None = None  # 精确高亮区域；缺省用节点 regions
    artifact_id: str | None = None
    role: Literal["primary", "context", "definition", "condition", "contradiction"] = "primary"


class QualityIssue(BaseModel):
    code: str  # bbox_out_of_bounds / overlap / caption_mismatch / absorbed_by_merge / asset_missing / ...
    severity: Literal["info", "warning", "error"] = "warning"
    node_id: str | None = None
    page: int | None = None
    message: str = ""
    details: dict[str, Any] = Field(default_factory=dict)


class QualityReport(BaseModel):
    issues: list[QualityIssue] = Field(default_factory=list)
    page_count_layout: int = 0
    page_count_pdf: int | None = None
    scanned_pages: list[int] = Field(default_factory=list)
    middle_json_used: bool = False

    def counts(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for i in self.issues:
            out[i.code] = out.get(i.code, 0) + 1
        return out

    def summary(self) -> dict[str, Any]:
        return {
            "issue_counts": self.counts(),
            "page_count_layout": self.page_count_layout,
            "page_count_pdf": self.page_count_pdf,
            "scanned_pages": self.scanned_pages,
            "middle_json_used": self.middle_json_used,
        }


class DocumentMeta(BaseModel):
    title: str | None = None
    authors: list[str] = Field(default_factory=list)
    doi: str | None = None
    doi_source: str | None = None  # text / filename
    journal: str | None = None
    year: int | None = None
    volume: str | None = None
    pages: str | None = None


class PageInfo(BaseModel):
    page: int
    width_pt: float
    height_pt: float


class IRDocument(BaseModel):
    document_id: str
    revision: int = 1
    parser: str = "mineru_legacy"
    parser_version: str = "1"
    ir_version: str = "p2.1"
    pages: list[PageInfo] = Field(default_factory=list)
    nodes: list[DocumentNode] = Field(default_factory=list)
    relations: list[Relation] = Field(default_factory=list)
    meta: DocumentMeta = Field(default_factory=DocumentMeta)
    quality: QualityReport = Field(default_factory=QualityReport)

    def index(self) -> dict[str, DocumentNode]:
        return {n.local_id: n for n in self.nodes}

    def get(self, local_id: str) -> DocumentNode:
        for n in self.nodes:
            if n.local_id == local_id:
                return n
        raise KeyError(local_id)

    def of_type(self, *types: str) -> list[DocumentNode]:
        return [n for n in self.nodes if n.node_type in types]
