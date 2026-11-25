"""Conservative corrections and replacement guards for structured visual output."""

import copy
from typing import Any

from pydantic import BaseModel, ValidationError


def validate_semantic_output(value: Any, model: type[BaseModel]) -> Any:
    try:
        model.model_validate(value)
    except ValidationError as failure:
        status_errors = {
            "Value error, unresolved compound-series structures require status=unclear",
            "Value error, Outstanding information gaps require status=unclear",
        }
        if not (
            isinstance(value, dict)
            and value.get("status") == "ok"
            and all(not e["loc"] and e["msg"] in status_errors for e in failure.errors())
        ):
            raise
        corrected = copy.deepcopy(value)
        corrected["status"] = "unclear"
        model.model_validate(corrected)
        adjustments = corrected.setdefault("validation_adjustments", [])
        if isinstance(adjustments, list):
            adjustments.append(
                {
                    "field_path": "/status",
                    "original": "ok",
                    "corrected": "unclear",
                    "reason": "Unresolved members or information gaps require review; extraction content preserved.",
                }
            )
        return corrected
    return value


def reject_empty_replacement(previous: dict, candidate: dict) -> None:
    for key in ("pathways", "optimization_steps", "rows", "compound_instances"):
        if isinstance(previous.get(key), list) and previous[key] and not candidate.get(key):
            raise ValueError(
                f"extraction_content_lost: {key} became empty; previous extraction retained. "
                "Return the full extraction with all existing routes and member evidence, "
                "or explicitly investigate contradictory evidence before replacing it."
            )
