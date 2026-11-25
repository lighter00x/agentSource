"""API 响应模型（进入 OpenAPI，供前端生成客户端）。"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from drugpaper.domain.geometry import Region


class DocumentCreated(BaseModel):
    document_id: str
    status: str
    duplicate: bool = False


class DocumentInfo(BaseModel):
    document_id: str
    status: str
    filename: str | None = None
    title: str | None = None
    doi: str | None = None
    active_revision: int | None = None
    meta: dict[str, Any] = Field(default_factory=dict)
    quality: dict[str, Any] = Field(default_factory=dict)  # 质量报告摘要
    error: dict[str, Any] | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class DocumentList(BaseModel):
    documents: list[DocumentInfo]


class TreeNode(BaseModel):
    id: str
    type: str
    subtype: str | None = None
    label: str | None = None
    page: int | None = None
    role: str | None = None
    text: str | None = None
    children: list[TreeNode] = Field(default_factory=list)


class DocumentTree(BaseModel):
    document_id: str
    revision: int
    root: TreeNode


class NodeBrief(BaseModel):
    node_id: str
    local_id: str
    type: str
    subtype: str | None = None
    label: str | None = None
    page: int | None = None
    section: str | None = None
    regions: list[Region] = Field(default_factory=list)
    preview: str | None = None


class PageNodes(BaseModel):
    document_id: str
    page: int
    nodes: list[NodeBrief]


class NodeDetail(BaseModel):
    node_id: str
    local_id: str
    document_id: str
    revision: int
    type: str
    subtype: str | None = None
    label: str | None = None
    page: int | None = None
    section: str | None = None
    reading_order: int
    regions: list[Region]
    text_raw: str | None = None
    text_norm: str | None = None
    parent: str | None = None
    children: list[NodeBrief] = Field(default_factory=list)
    quality_flags: list[str] = Field(default_factory=list)
    attributes: dict[str, Any] = Field(default_factory=dict)
    source_ref: dict[str, Any] = Field(default_factory=dict)


class SearchResultItem(BaseModel):
    node_id: str
    page: int | None
    type: str
    section: str | None
    snippet: str
    score: float | None = None
    char_span: tuple[int, int] | None = None


class SearchResponse(BaseModel):
    document_id: str
    query: str
    mode: str
    results: list[SearchResultItem]
    truncated: bool = False
