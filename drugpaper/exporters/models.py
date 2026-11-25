from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class FileDigest(BaseModel):
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    bytes: int = Field(ge=0)


class ExportManifest(BaseModel):
    format_version: str
    app_version: str
    profile: str
    profile_version: str
    document_id: str
    revision: int
    fingerprint: str
    result_id: str
    status: str
    components: dict[str, str]
    artifacts: list[dict[str, Any]]
    models: list[dict[str, Any]]
    call_metrics: list[dict[str, Any]]
    checks: list[dict[str, Any]]
    warnings: list[str]
    files: dict[str, FileDigest]
