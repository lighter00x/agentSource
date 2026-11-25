"""Shared response for authenticated figure-result downloads."""

import json
from typing import Any

from fastapi.responses import JSONResponse

from drugpaper.facts.figure_result import result_filename
from drugpaper.facts.flowchart_delivery import compact_result, summarize_result


class ResultJSONResponse(JSONResponse):
    def render(self, content: Any) -> bytes:
        return (json.dumps(content, ensure_ascii=False, allow_nan=False, indent=2) + "\n").encode("utf-8")


def result_response(result: dict[str, Any], download: bool, view: str = "summary") -> JSONResponse:
    headers = {"Cache-Control": "no-store"}
    if download:
        headers["Content-Disposition"] = f'attachment; filename="{result_filename(result)}"'
    output = (
        result
        if view == "full" or result.get("processing_stage") == "backfilled"
        else compact_result(result)
        if view == "delivery"
        else summarize_result(result)
    )
    return ResultJSONResponse(output, headers=headers)
