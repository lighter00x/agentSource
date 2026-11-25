"""Quarantine invalid visual links without inventing replacements or discarding molecules."""

from __future__ import annotations

import copy
from typing import Any


def quarantine_links(payload: dict[str, Any]) -> dict[str, Any]:
    result = copy.deepcopy(payload)
    errors: list[dict[str, Any]] = []

    def labels(entities: list[dict[str, Any]]) -> set[str]:
        return {
            label
            for entity in entities
            for label in (
                [v["reference"] for v in entity.get("variants", [])]
                if entity.get("variants")
                else [entity.get("reference")]
            )
            if label
        }

    def walk(value: Any, path: str) -> None:
        if isinstance(value, list):
            for i, child in enumerate(value):
                walk(child, f"{path}[{i}]")
        elif isinstance(value, dict):
            if "reactants" in value and "products" in value:
                sources, targets = labels(value["reactants"]), labels(value["products"])
                valid, rejected = [], []
                for link in value.get("variant_links", []):
                    if link["source"] in sources and link["target"] in targets:
                        valid.append(link)
                    else:
                        rejected.append(link)
                        errors.append(
                            {
                                "code": "invalid_variant_link",
                                "field_path": path,
                                "reason": "Endpoint belongs to another arrow",
                                "rejected_link": link,
                            }
                        )
                if rejected:
                    value["variant_links"] = valid
                    value["rejected_variant_links"] = rejected
            for key, child in list(value.items()):
                if key != "rejected_variant_links":
                    walk(child, f"{path}.{key}")

    walk(result, "$")
    if not errors:
        raise ValueError("No local link error can be safely quarantined")
    result["status"] = "unclear"
    result["needs_review"] = True
    result["semantic_errors"] = errors
    return result
