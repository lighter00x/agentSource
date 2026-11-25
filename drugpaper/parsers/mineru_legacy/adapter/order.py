"""S2.4d：阅读顺序（设计文档 7.3 第 8 步）。

页内按 layout 中块的出现顺序（与 confirmed_index 一致，合并组成员紧随组）、跨页按页序；
树按先序遍历编号，子节点按排序键排列；续段 region 不改变顺序。丢弃块排在主树之后。
"""

from __future__ import annotations

from drugpaper.domain import ids
from drugpaper.parsers.mineru_legacy.adapter.context import BuildContext


def assign_reading_order(ctx: BuildContext) -> None:
    counter = 0
    visited: set[str] = set()

    def visit(local_id: str) -> None:
        nonlocal counter
        if local_id in visited:
            return
        visited.add(local_id)
        node = ctx.nodes[local_id]
        node.reading_order = counter
        counter += 1
        node.children.sort(key=lambda c: ctx.order.get(c, 0.0))
        for c in node.children:
            visit(c)

    if ids.ROOT_ID in ctx.nodes:
        visit(ids.ROOT_ID)
    rest = sorted((n for n in ctx.nodes if n not in visited), key=lambda n: ctx.order.get(n, 0.0))
    for n in rest:
        visit(n)
