from __future__ import annotations

import re
from typing import Any

from drugpaper.domain.nodes import IRDocument
from drugpaper.facts.labels import expand_range


def build_v1(ir: IRDocument) -> dict[str, Any]:
    labels = set()
    captions = {}
    references = {}
    body = [n for n in ir.nodes if n.node_type in {"paragraph", "heading"}]
    text = "\n".join(n.text_norm or n.text_raw or "" for n in body)
    for match in re.finditer(r"\b(?:compounds?\s+)?(\d+[a-z]?(?:[−–-]\d+)?)\b", text, re.I):
        raw = match[1]
        try:
            labels.update(expand_range(raw))
        except ValueError:
            labels.add(raw)
    visuals = [
        n
        for n in ir.nodes
        if n.node_type in {"figure", "table", "scheme"} and n.status != "removed" and n.geometry.regions
    ]
    for node in visuals:
        caption = node.label or node.text_raw or ""
        captions[node.ref] = caption
        figure = re.search(r"(Figure|Table|Scheme)\s+(\d+)", caption, re.I)
        reference_pattern = (
            re.compile(r"\b" + figure[1] + r"\s+" + figure[2] + r"\b", re.I) if figure else None
        )
        references[node.ref] = [
            n.text_norm or n.text_raw or ""
            for n in body
            if reference_pattern and reference_pattern.search(n.text_norm or n.text_raw or "")
        ][:4]
    return {
        "version": 1,
        "document_id": ir.document_id,
        "revision": ir.revision,
        "title": ir.meta.title,
        "abstract": ir.meta.model_dump().get("abstract"),
        "labels": sorted(labels),
        "captions": captions,
        "references": references,
        "target_candidates": sorted(set(re.findall(r"\b(?:D[1-5]R|hERG|CYP\w+|[A-Z]\w+ receptor)\b", text))),
    }


def build_v2(v1: dict[str, Any], compounds: list[dict[str, Any]]) -> dict[str, Any]:
    return {**v1, "version": 2, "compounds": compounds}
