"""S2.4a：图注标签识别与节点类型判定（设计文档 7.3 第 3 步，F2）。"""

from __future__ import annotations

import re
from dataclasses import dataclass

from drugpaper.domain.nodes import NodeType
from drugpaper.text.normalize import normalize_text

# 沿用现有流水线 `^(Figure|Fig\.|Table|Scheme)\s*[IVX0-9]+[A-Za-z]?`，加入 Chart 与补充材料 S 前缀
LABEL_RE = re.compile(r"^(Figure|Fig\.|Scheme|Table|Chart)\s*(S?(?:\d+|[IVX]+)[A-Za-z]?)\b\.?", re.IGNORECASE)
# 脚注：以小写标记字母开头、紧跟大写字母（aReagents、a Effect、^ { a } Values）
FOOTNOTE_RE = re.compile(r"^(?:\^\s*\{\s*)?([a-z])(?:\s*\})?\s*(?=[A-Z])")

LABEL_KIND_TO_TYPE: dict[str, NodeType] = {
    "table": "table",
    "scheme": "scheme",
    "figure": "figure",
    "fig.": "figure",
    "chart": "figure",
}

RAW_TYPE_MAP: dict[str, NodeType] = {
    "title": "heading",
    "text": "paragraph",
    "list": "list",
    "image_body": "figure",
    "image": "figure",
    "table_body": "table",
    "table": "table",
    "interline_equation": "formula",
    "equation": "formula",
}

VISUAL_RAW_TYPES = frozenset({"image_body", "image", "table_body", "table"})
TEXT_RAW_TYPES = frozenset({"text", "title", "list"})


@dataclass(frozen=True)
class Label:
    kind: str  # table / scheme / figure / chart
    number: str  # "1"、"S2"、"3A"
    text: str  # 规范化后的标签，如 "Table 1"

    @property
    def node_type(self) -> NodeType:
        return LABEL_KIND_TO_TYPE[self.kind]


def find_label(text: str | None) -> Label | None:
    if not text:
        return None
    t = normalize_text(text)
    m = LABEL_RE.match(t)
    if m is None:
        return None
    word = m.group(1).lower()
    kind = "figure" if word == "fig." else word
    canonical = {"figure": "Figure", "scheme": "Scheme", "table": "Table", "chart": "Chart"}[kind]
    return Label(kind=kind, number=m.group(2), text=f"{canonical} {m.group(2)}")


def is_footnote_text(text: str | None) -> bool:
    return bool(text) and FOOTNOTE_RE.match(text.strip()) is not None  # type: ignore[union-attr]


def classify_visual(
    label: Label | None,
    raw_type: str,
    *,
    parent_type: str | None = None,
    in_abstract_area: bool = False,
) -> tuple[NodeType, str | None]:
    """返回 (node_type, subtype)。

    Table → table；Scheme → scheme；Figure/Chart → figure；
    无标签且位于首页摘要区域 → figure(toc_graphic)；否则按 parent_type / 块类型。
    """
    if label is not None:
        return label.node_type, None
    if in_abstract_area:
        return "figure", "toc_graphic"
    base = (parent_type or raw_type or "").lower()
    if base.startswith("table"):
        return "table", None
    return "figure", None


def parse_footnote_marks(text: str) -> dict[str, str]:
    """`aEffect over ... bValues are ...` → {"a": "Effect over ...", "b": "Values are ..."}。"""
    t = normalize_text(text)
    parts = re.split(r"(?:^|(?<=[.;)])\s*)(?:\^)?([a-z]) ?(?=[A-Z])", t)
    marks: dict[str, str] = {}
    # re.split 带分组：[前缀, 标记1, 内容1, 标记2, 内容2, ...]
    for i in range(1, len(parts) - 1, 2):
        mark, body = parts[i], parts[i + 1].strip()
        if body and mark not in marks:
            marks[mark] = body
    return marks
