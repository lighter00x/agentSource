from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from drugpaper.agents.figure.accounting import CallAccounting
from drugpaper.facts.figure_models import ReviewResult
from drugpaper.validators.figure.codes import CheckResult


class FigureReport(BaseModel):
    result_file: dict[str, Any] | None = None
    report_id: str
    document_id: str
    node_ref: str
    mode: Literal["full", "query", "review"] = "full"
    image_type: str = "other"
    status: Literal["ok", "remediated", "needs_review", "failed"] = "needs_review"
    confidence: float | None = None
    artifact_ids: list[str] = Field(default_factory=list)
    exploration: dict[str, Any] = Field(default_factory=dict)
    extraction: dict[str, Any] = Field(default_factory=dict)
    pre_backfill_extraction: dict[str, Any] = Field(default_factory=dict)
    backfilled_extraction: dict[str, Any] = Field(default_factory=dict)
    molecular_coverage: dict[str, Any] = Field(default_factory=dict)
    image_products: list[dict[str, Any]] = Field(default_factory=list)
    checks: list[CheckResult] = Field(default_factory=list)
    review: ReviewResult = Field(default_factory=ReviewResult)
    unchecked: list[dict[str, Any]] = Field(default_factory=list)
    open_questions: list[str] = Field(default_factory=list)
    accounting: CallAccounting = Field(default_factory=CallAccounting)
    trace: list[dict[str, Any]] = Field(default_factory=list)
    child_reports: list[dict[str, Any]] = Field(default_factory=list)
    query_answers: list[dict[str, Any]] = Field(default_factory=list)
    run_id: str
