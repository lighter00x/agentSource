"""解析产物的路径处理：穿越过滤与资产分类。"""

from __future__ import annotations

import re

_DRIVE = re.compile(r"^[A-Za-z]:")


def safe_rel_path(rel: str) -> str | None:
    """拒绝 `..`、绝对路径、盘符；返回规范化的 `a/b/c` 形式，非法时返回 None。"""
    if not rel or rel.startswith(("/", "\\")) or _DRIVE.match(rel):
        return None
    parts = rel.replace("\\", "/").split("/")
    if any(p == ".." for p in parts):
        return None
    clean = [p for p in parts if p and p != "."]
    return "/".join(clean) if clean else None


def asset_kind(rel: str) -> str:
    name = rel.rsplit("/", 1)[-1].lower()
    top = rel.split("/", 1)[0].lower() if "/" in rel else ""
    if top == "images":
        return "image"
    if top == "merged_images":
        return "merged_image"
    if top.startswith("debug"):
        return "debug"
    if name.endswith("_layout.json"):
        return "raw_layout_json"
    if name.endswith("_middle.json"):
        return "raw_middle_json"
    if name.endswith("_content_list.json"):
        return "raw_content_list"
    if name.endswith("_model.json"):
        return "raw_model_json"
    if name.endswith("_origin.pdf"):
        return "origin_pdf"
    if name.endswith(".md"):
        return "raw_md"
    if name.endswith(".pdf"):
        return "debug_pdf"
    return "other"


# 离线导入与转存时跳过的类别（调试产物体积大、对下游无用）
SKIPPED_KINDS = frozenset({"debug", "debug_pdf"})
