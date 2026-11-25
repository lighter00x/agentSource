"""Standalone, versioned delivery of a single figure's backfilled content."""

from __future__ import annotations

import copy
import json
import re
from typing import Any, Literal

from drugpaper.facts.flowchart_delivery import compact_result, summarize_result
from drugpaper.storage.base import BlobStore


def build_result(
    source: dict[str, Any], stage: Literal["backfilled", "reported"] = "reported"
) -> dict[str, Any]:
    content = source.get("backfilled_extraction")
    if not isinstance(content, dict) or not content:
        raise ValueError("图表尚未生成回填后的解析结果")
    return {
        "schema_version": "figure-result/1.0",
        "document_id": source["document_id"],
        "node_ref": source["node_ref"],
        "run_id": source["run_id"],
        "report_id": source.get("report_id"),
        "image_type": source.get("image_type") or source.get("classification", {}).get("image_type", "other"),
        "processing_stage": stage,
        "status": source.get("status", "needs_review") if stage == "reported" else "pending_review",
        "extraction": copy.deepcopy(content),
        "exploration": copy.deepcopy(source.get("exploration", {})),
        "quality": {
            key: copy.deepcopy(source.get(key, {} if key in {"review", "molecular_coverage"} else []))
            for key in ("checks", "review", "unchecked", "open_questions", "molecular_coverage")
        },
    }


def result_filename(source: dict[str, Any]) -> str:
    local = source["node_ref"].rsplit("/", 1)[-1]
    return re.sub(r"[^a-zA-Z0-9_.-]", "_", f"{local}-{source['run_id']}-result.json")


async def persist_result(
    store: BlobStore, source: dict[str, Any], stage: Literal["backfilled", "reported"] = "reported"
) -> tuple[dict[str, Any], dict[str, Any]]:
    result = build_result(source, stage)
    data = (json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode()
    ref = await store.put(data, "application/json")
    metadata = {
        "blob_ref": ref,
        "filename": result_filename(source),
        "schema_version": result["schema_version"],
        "processing_stage": stage,
        "status": result["status"],
    }
    if stage == "backfilled":
        return metadata, result
    delivery = compact_result(result)
    delivery_ref = await store.put(
        (json.dumps(delivery, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode(),
        "application/json",
    )
    summary = summarize_result(result)
    summary_ref = await store.put(
        (json.dumps(summary, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode(),
        "application/json",
    )
    return {
        **metadata,
        "delivery_blob_ref": delivery_ref,
        "summary_blob_ref": summary_ref,
        "summary_schema_version": summary["schema_version"],
        "delivery_schema_version": delivery["schema_version"],
    }, result
