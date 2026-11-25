"""P6 正文抽取：模型偏移以规范化文本为准，交付证据以原文为准。"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, StrictInt

from drugpaper.domain.nodes import EvidenceRef


class SpanItem(BaseModel):
    model_config = ConfigDict(extra="forbid")
    char_span: tuple[StrictInt, StrictInt]
    evidence_text: str | None = None
    evidence: list[EvidenceRef] = Field(default_factory=list)


class Mention(SpanItem):
    label: str
    aliases: list[str] = Field(default_factory=list)
    role: Literal["hit", "lead", "final", "analog", "reference", "unknown"] = "unknown"


class TextMeasurement(SpanItem):
    compound_label: str
    metric_text: str
    value_raw: str
    unit: str | None = None
    conditions: str | None = None


class AssayText(SpanItem):
    text: str


class Statement(SpanItem):
    text: str
    from_label: str | None = None
    to_label: str | None = None


class ExperimentalName(SpanItem):
    name: str
    label: str


class TextExtraction(BaseModel):
    model_config = ConfigDict(extra="forbid")
    compound_mentions: list[Mention] = Field(default_factory=list)
    measurements: list[TextMeasurement] = Field(default_factory=list)
    assay_descriptions: list[AssayText] = Field(default_factory=list)
    optimization_statements: list[Statement] = Field(default_factory=list)
    experimental_names: list[ExperimentalName] = Field(default_factory=list)
    discarded_count: int = 0
    candidate_count: int = 0
    span_repair_count: int = 0
    source_text_sha256: str = ""
    status: Literal["completed", "needs_review", "skipped"] = "completed"
