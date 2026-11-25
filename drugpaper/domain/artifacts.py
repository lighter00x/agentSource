"""过程模型：Artifact 与依赖（设计文档 6.10 节）。"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel

ArtifactStatus = Literal[
    "completed", "validated", "needs_review", "stale", "superseded", "failed", "human_corrected"
]
DependencyKind = Literal["input", "context", "validation"]


class Artifact(BaseModel):
    artifact_id: str
    document_id: str
    artifact_type: str
    target_ref: str
    work_unit_key: str
    version: int
    schema_name: str | None = None
    schema_version: str | None = None
    payload: dict[str, Any] | None = None
    blob_ref: str | None = None
    input_fingerprint: str | None = None
    produced_by_run_id: str | None = None
    status: ArtifactStatus = "completed"
    is_active: bool = True
    confidence: float | None = None
    created_at: datetime | None = None


class ArtifactDependency(BaseModel):
    upstream: str  # artifact_id 或 "node:<ref>@<geometry_hash>" 或 "correction:<id>"
    downstream: str  # artifact_id
    kind: DependencyKind = "input"
