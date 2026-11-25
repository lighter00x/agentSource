"""ID 规则（设计文档 6.2 节）。

| 对象 | 格式 |
|---|---|
| Document | `doc_{ULID}` |
| 节点局部 ID | 原始块 `p{page:03d}.b{di:03d}`；页内合并组 `p{page:03d}.g{ci:03d}`；跨页合并组 `x{page:03d}.g{ci:03d}`；
  图注 `{parent}.c{k}`；脚注 `{parent}.f{k}`；图内部件 `{parent}.p{k}`；小标题 `{parent}.h0`；丢弃块 `p{page:03d}.d{k:03d}`；
  章节 `s{k:03d}`；文档根 `doc`；前置信息 `front` |
| 节点全局引用 | `{document_id}/r{rev}/{local_id}` |
"""

from __future__ import annotations

import os
import re
import time

_CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"

ROOT_ID = "doc"
FRONT_ID = "front"

GLOBAL_REF_RE = re.compile(r"^(?P<doc>[A-Za-z0-9_\-]+)/r(?P<rev>\d+)/(?P<local>[A-Za-z0-9_.\-]+)$")


def ulid(now_ms: int | None = None) -> str:
    """26 位 Crockford base32：48 位毫秒时间戳 + 80 位随机数，按时间可排序。"""
    ts = int(time.time() * 1000) if now_ms is None else now_ms
    value = (ts << 80) | int.from_bytes(os.urandom(10), "big")
    out = []
    for _ in range(26):
        out.append(_CROCKFORD[value & 31])
        value >>= 5
    return "".join(reversed(out))


def new_document_id() -> str:
    return f"doc_{ulid()}"


def new_id(prefix: str) -> str:
    return f"{prefix}_{ulid()}"


def block_id(page: int, detailed_index: int) -> str:
    return f"p{page:03d}.b{detailed_index:03d}"


def group_id(page: int, confirmed_index: int) -> str:
    return f"p{page:03d}.g{confirmed_index:03d}"


def cross_group_id(first_page: int, confirmed_index: int) -> str:
    return f"x{first_page:03d}.g{confirmed_index:03d}"


def caption_id(parent: str, k: int) -> str:
    return f"{parent}.c{k}"


def footnote_id(parent: str, k: int) -> str:
    return f"{parent}.f{k}"


def part_id(parent: str, k: int) -> str:
    return f"{parent}.p{k}"


def run_in_heading_id(parent: str) -> str:
    return f"{parent}.h0"


def discarded_id(page: int, k: int) -> str:
    return f"p{page:03d}.d{k:03d}"


def section_id(k: int) -> str:
    return f"s{k:03d}"


def global_ref(document_id: str, revision: int, local_id: str) -> str:
    return f"{document_id}/r{revision}/{local_id}"


def parse_ref(ref: str) -> tuple[str, int, str] | None:
    """全局引用 → (document_id, revision, local_id)；不是全局引用时返回 None。"""
    m = GLOBAL_REF_RE.match(ref)
    if m is None:
        return None
    return m.group("doc"), int(m.group("rev")), m.group("local")
