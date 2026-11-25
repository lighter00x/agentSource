"""Optional OPSIN JSON HTTP service; warnings never become silently verified structures."""

from __future__ import annotations

from typing import Literal
from urllib.parse import quote

import httpx2
from pydantic import BaseModel, Field


class OpsinResult(BaseModel):
    status: Literal["success", "warning", "failure", "unavailable"]
    smiles: str | None = None
    message: str = ""
    warnings: list[str] = Field(default_factory=list)


class OpsinClient:
    def __init__(
        self,
        base_url: str = "",
        *,
        timeout_s: float = 20,
        http_client: httpx2.AsyncClient | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout_s = timeout_s
        self._http = http_client

    async def parse(self, name: str) -> OpsinResult:
        if not name.strip() or len(name) > 4000:
            return OpsinResult(status="failure", message="invalid_name")
        if not self.base_url:
            return OpsinResult(status="unavailable", message="OPSIN_BASE_URL not configured")
        try:
            if self._http is None:
                self._http = httpx2.AsyncClient(timeout=self.timeout_s, trust_env=False)
            response = await self._http.get(
                f"{self.base_url}/{quote(name, safe='')}.json", timeout=self.timeout_s
            )
            # OPSIN can return a structured FAILURE with HTTP 404.
            if response.status_code not in {200, 404}:
                return OpsinResult(status="unavailable", message=f"opsin_http_{response.status_code}")
            data = response.json()
            if not isinstance(data, dict):
                raise ValueError("invalid_opsin_response")
            status = str(data.get("status", "")).lower()
            if status not in {"success", "warning", "failure"}:
                raise ValueError("invalid_opsin_status")
            smiles = data.get("smiles")
            if status != "failure" and (not isinstance(smiles, str) or not smiles.strip()):
                raise ValueError("opsin_missing_smiles")
            warnings = data.get("warnings") or []
            if isinstance(warnings, str):
                warnings = [warnings]
            return OpsinResult(
                status=status,
                smiles=smiles if status != "failure" else None,
                message=str(data.get("message") or ""),
                warnings=warnings,
            )
        except (httpx2.HTTPError, ImportError, ValueError, TypeError):
            # Do not include request URLs (chemical names) or service bodies in errors.
            return OpsinResult(status="unavailable", message="opsin_transport_or_protocol_error")

    async def aclose(self) -> None:
        if self._http is not None:
            await self._http.aclose()
