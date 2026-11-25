from __future__ import annotations

import json
from typing import Any

from drugpaper.capabilities.tokens import estimate_tokens
from drugpaper.facts.full_text import FullTextContext


def build_chunks(
    ctx: FullTextContext, audit: dict[str, Any], *, max_tokens: int = 800
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []

    def add(
        kind: str, text: str, evidence: list[dict[str, Any]], section: str | None, source_ids: list[str]
    ) -> None:
        # Split long semantic units by text, retaining all pointers and original character offsets.
        start = 0
        while start < len(text):
            end = len(text)
            while estimate_tokens(text[start:end]) > max_tokens:
                end = start + max(1, (end - start) // 2)
            out.append(
                {
                    "chunk_id": f"chunk_{len(out):06d}",
                    "document_id": ctx.ir.document_id,
                    "revision": ctx.ir.revision,
                    "kind": kind,
                    "section": section,
                    "text": text[start:end],
                    "text_span": [start, end],
                    "evidence": evidence,
                    "source_ids": source_ids,
                    "estimated_tokens": estimate_tokens(text[start:end]),
                }
            )
            start = end

    current: list[str] = []
    current_evidence: list[dict[str, Any]] = []
    section = None
    for node in sorted(ctx.ir.nodes, key=lambda n: (n.reading_order, n.local_id)):
        if (
            node.status == "removed"
            or not node.text_raw
            or node.node_type in {"document", "section", "front_matter", "discarded"}
        ):
            continue
        if current and (
            section != node.section_role
            or estimate_tokens("\n\n".join([*current, node.text_raw])) > max_tokens
        ):
            add("text", "\n\n".join(current), current_evidence, section, [])
            current, current_evidence = [], []
        section = node.section_role
        current.append(node.text_raw)
        current_evidence.append(
            {"node_id": node.ref, "role": "primary", "char_span": [0, len(node.text_raw)]}
        )
    if current:
        add("text", "\n\n".join(current), current_evidence, section, [])
    for c in ctx.compounds:
        add(
            "compound",
            json.dumps(c, ensure_ascii=False),
            c.get("evidence", []) or (c.get("accepted_structure") or {}).get("evidence", []),
            None,
            [c["compound_id"]],
        )
    for path, field in audit.get("_audit", {}).get("field_sources", {}).items():
        if field.get("value") != "null":
            add(
                "profile_field",
                path + ": " + str(field["value"]),
                field["evidence"],
                None,
                field.get("source_ids", []),
            )
    return out
