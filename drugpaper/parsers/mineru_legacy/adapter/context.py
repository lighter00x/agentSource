"""IR Builder 各步骤共享的构建上下文。"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Literal

from drugpaper.domain.geometry import BBox, Region, pt_out_of_bounds, pt_to_norm
from drugpaper.domain.nodes import DocumentNode, NodeType, QualityIssue, Relation
from drugpaper.parsers.mineru_legacy.schemas import LayoutBlock, LayoutPage, MiddleDoc

PtBox = tuple[float, float, float, float]


@dataclass
class EmptyBlock:
    """文本为空的原始块（F5：magic_pdf 续段合并后留下的空壳）。本身不生成节点。"""

    page: int
    detailed_index: int
    bbox_pt: PtBox
    block_type: str
    member_of: str | None = None  # 所属合并组的局部 ID
    order: float = 0.0
    owner: str | None = None  # 认领它的段落节点


@dataclass
class TextSource:
    """由原始块生成的文本节点（段落、列表、标题），供续段与行级增强使用。"""

    local_id: str
    page: int
    detailed_index: int
    bbox_pt: PtBox


@dataclass
class BuildContext:
    document_id: str
    revision: int
    pages: list[LayoutPage]
    middle: MiddleDoc | None = None
    nodes: dict[str, DocumentNode] = field(default_factory=dict)
    order: dict[str, float] = field(default_factory=dict)  # 局部 ID → 排序键
    relations: list[Relation] = field(default_factory=list)
    issues: list[QualityIssue] = field(default_factory=list)
    empties: dict[tuple[int, int], EmptyBlock] = field(default_factory=dict)
    text_sources: dict[str, TextSource] = field(default_factory=dict)

    pdf_page_sizes: list[tuple[float, float]] | None = None

    def __post_init__(self) -> None:
        # 归一化用的页面尺寸：提供了 PDF 真实页面尺寸时以 PDF 为准（bbox 是 PDF 点坐标，
        # 若 layout.json 的 page_size 与 PDF 不一致，按 page_size 归一化会让裁剪偏移）。
        self.page_sizes: dict[int, tuple[float, float]] = {p.page_idx: p.page_size for p in self.pages}
        if self.pdf_page_sizes:
            for p in self.pages:
                if p.page_idx < len(self.pdf_page_sizes):
                    self.page_sizes[p.page_idx] = self.pdf_page_sizes[p.page_idx]

    # ------------------------------------------------------------ 坐标

    def norm(self, page: int, bbox_pt: Sequence[float]) -> BBox:
        return pt_to_norm(bbox_pt, self.page_sizes[page])

    def region(
        self,
        page: int,
        bbox_pt: Sequence[float],
        role: Literal["primary", "continuation", "segment"] = "primary",
        *,
        derived: bool = False,
    ) -> Region:
        pt = (float(bbox_pt[0]), float(bbox_pt[1]), float(bbox_pt[2]), float(bbox_pt[3]))
        return Region(page=page, bbox=self.norm(page, pt), source_bbox_pt=pt, role=role, derived=derived)

    def out_of_bounds(self, page: int, bbox_pt: Sequence[float]) -> bool:
        return pt_out_of_bounds(bbox_pt, self.page_sizes[page])

    # ------------------------------------------------------------ 节点

    def add(
        self,
        local_id: str,
        node_type: NodeType,
        *,
        order: float,
        regions: list[Region] | None = None,
        parent_id: str | None = None,
        text_raw: str | None = None,
        label: str | None = None,
        subtype: str | None = None,
        source_ref: dict[str, Any] | None = None,
        attributes: dict[str, Any] | None = None,
    ) -> DocumentNode:
        if local_id in self.nodes:
            # 源数据异常（同页重复序号）：保留第一个，记录告警
            self.issue("duplicate_id", node_id=local_id, message=f"重复的节点 ID：{local_id}")
            local_id = f"{local_id}.dup{sum(1 for k in self.nodes if k.startswith(local_id))}"
        node = DocumentNode(
            document_id=self.document_id,
            revision=self.revision,
            local_id=local_id,
            node_type=node_type,
            subtype=subtype,
            parent_id=parent_id,
            label=label,
            text_raw=text_raw,
            source_ref=source_ref or {},
            attributes=attributes or {},
        )
        node.geometry.regions = regions or []
        self.nodes[local_id] = node
        self.order[local_id] = order
        if parent_id is not None and parent_id in self.nodes:
            self.nodes[parent_id].children.append(local_id)
        return node

    def reparent(self, local_id: str, new_parent: str | None) -> None:
        node = self.nodes[local_id]
        if node.parent_id and node.parent_id in self.nodes:
            siblings = self.nodes[node.parent_id].children
            if local_id in siblings:
                siblings.remove(local_id)
        node.parent_id = new_parent
        if new_parent is not None and local_id not in self.nodes[new_parent].children:
            self.nodes[new_parent].children.append(local_id)

    def relate(self, source: str, target: str, rel_type: Any, **evidence: Any) -> None:
        self.relations.append(Relation(source=source, target=target, type=rel_type, evidence=evidence))

    def issue(
        self,
        code: str,
        *,
        node_id: str | None = None,
        page: int | None = None,
        message: str = "",
        severity: Literal["info", "warning", "error"] = "warning",
        **details: Any,
    ) -> None:
        self.issues.append(
            QualityIssue(
                code=code, severity=severity, node_id=node_id, page=page, message=message, details=details
            )
        )

    @staticmethod
    def source_ref(page: int, b: LayoutBlock, **more: Any) -> dict[str, Any]:
        ref: dict[str, Any] = {
            "page_idx": page,
            "detailed_index": b.detailed_index,
            "confirmed_index": b.confirmed_index,
            "block_type": b.type,
            "bbox_pt": list(b.bbox),
        }
        extra = b.extra_fields()
        if extra:
            ref["extra"] = extra
        ref.update({k: v for k, v in more.items() if v is not None})
        return ref
