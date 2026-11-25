"""共享 Worker 批量协议；坏图和缺失结果保留 input_index。"""

from __future__ import annotations

import asyncio
import base64
from collections.abc import Sequence
from typing import Any

import httpx2

from drugpaper.gateway.molparser import MolParserClient


class MolDetClient:
    def __init__(self, base_url: str, token: str, *, http_client: httpx2.AsyncClient | None = None) -> None:
        self.base_url, self.token = base_url.rstrip("/"), token
        self.client = http_client or httpx2.AsyncClient(timeout=150, trust_env=False)

    async def health(self) -> dict[str, Any]:
        response = await self.client.get(self.base_url + "/health")
        response.raise_for_status()
        return dict(response.json())

    async def detect(self, images: Sequence[bytes], *, user_id: str = "default") -> list[dict[str, Any]]:
        output: list[dict[str, Any]] = []
        for start in range(0, len(images), 32):
            batch = images[start : start + 32]
            for attempt in range(3):
                try:
                    response = await self.client.post(
                        self.base_url + "/detect/batch",
                        headers={"X-Service-Token": self.token},
                        json={
                            "user_id": user_id,
                            "images": [base64.b64encode(data).decode() for data in batch],
                        },
                    )
                    response.raise_for_status()
                    break
                except httpx2.HTTPError as exc:
                    if (
                        attempt == 2
                        or isinstance(exc, httpx2.HTTPStatusError)
                        and exc.response.status_code < 500
                    ):
                        raise
                    await asyncio.sleep(0.25 * 2**attempt)
            records = MolParserClient._align(response.json().get("results", []), len(batch))
            output.extend(
                {**records.get(i, {"boxes": [], "error": "missing_result"}), "input_index": start + i}
                for i in range(len(batch))
            )
        return output

    async def aclose(self) -> None:
        await self.client.aclose()
