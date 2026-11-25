from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from drugpaper.domain.nodes import EvidenceRef


class LookupQuestion(BaseModel):
    model_config = ConfigDict(extra="forbid")
    question: str = Field(min_length=1, max_length=500)
    kind: Literal["text", "vision"] = "text"
    target_page: int | None = Field(default=None, ge=0)
    blocking: bool = False


class LookupResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    question: str
    answer: str = Field(default="", max_length=150)
    status: Literal["found", "not_found", "ambiguous", "need_vision"] = "not_found"
    evidence: list[EvidenceRef] = Field(default_factory=list)
    suggested_pages: list[int] = Field(default_factory=list)
    lookup_run_id: str = ""


class LookupBatchResult(BaseModel):
    results: list[LookupResult] = Field(default_factory=list)
    model_calls: int = 0
    tool_calls: int = 0
    cache_hits: int = 0
    batch_run_id: str
    warnings: list[str] = Field(default_factory=list)
    model_records: list[dict[str, Any]] = Field(default_factory=list)


class LookupAction(BaseModel):
    model_config = ConfigDict(extra="forbid")
    question_id: int = Field(ge=0, le=7)
    tool: str
    args: dict[str, Any] = Field(default_factory=dict)


class LookupPlan(BaseModel):
    actions: list[LookupAction] = Field(default_factory=list, max_length=20)


class LookupAnswers(BaseModel):
    results: list[LookupResult] = Field(default_factory=list, max_length=8)
