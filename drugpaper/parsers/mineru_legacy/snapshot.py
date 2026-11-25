"""IR 快照：用于回归测试的精简视图（节点类型、标签、层级、章节、region 数、质量标记）。"""

from __future__ import annotations

from typing import Any

from drugpaper.domain.nodes import IRDocument


def ir_snapshot(ir: IRDocument) -> dict[str, Any]:
    nodes = []
    for n in ir.nodes:
        nodes.append(
            {
                "id": n.local_id,
                "type": n.node_type,
                "subtype": n.subtype,
                "label": n.label,
                "parent": n.parent_id,
                "order": n.reading_order,
                "role": n.section_role,
                "regions": [[r.page, r.role, r.derived] for r in n.geometry.regions],
                "flags": sorted(n.quality_flags),
                "text": (n.text_norm or "")[:60],
            }
        )
    return {
        "document_id": ir.document_id,
        "ir_version": ir.ir_version,
        "meta": ir.meta.model_dump(),
        "quality": ir.quality.counts(),
        "node_count": len(ir.nodes),
        "type_counts": _type_counts(ir),
        "nodes": nodes,
    }


def _type_counts(ir: IRDocument) -> dict[str, int]:
    out: dict[str, int] = {}
    for n in ir.nodes:
        out[n.node_type] = out.get(n.node_type, 0) + 1
    return dict(sorted(out.items()))
