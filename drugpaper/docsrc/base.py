"""DocumentSource 协议：Agent 工具读取文档的唯一入口。

P1 实现：`layout_json.LayoutJsonSource`（直接读 MinerU 的 layout.json）；
P2 替换为基于 Document IR + 数据库的实现，块 ID 规则保持一致（设计文档 6.2 节）。
"""

from __future__ import annotations

from typing import Literal, Protocol

from pydantic import BaseModel, Field

BBox = tuple[float, float, float, float]  # 归一化到 [0,1]：x0, y0, x1, y1（左上原点）


class BlockView(BaseModel):
    id: str  # p{page:03d}.b{detailed_index:03d} 或 p{page:03d}.g{confirmed_index:03d}
    page: int
    type: str  # MinerU 块类型：text / title / list / image_body / table_body / ...
    kind: Literal["block", "group"] = "block"
    text: str
    bbox: BBox | None = None
    members: list[str] = Field(default_factory=list)  # 合并组成员的块 ID


class Hit(BaseModel):
    block_id: str
    page: int
    type: str
    snippet: str


class DocumentSource(Protocol):
    @property
    def document_id(self) -> str: ...

    def pages(self) -> list[int]: ...

    def blocks(self, page: int | None = None) -> list[BlockView]: ...

    def block(self, block_id: str) -> BlockView:
        """块不存在时抛出 KeyError。"""
        ...

    def search_text(self, pattern: str, max_results: int = 10) -> list[Hit]: ...
