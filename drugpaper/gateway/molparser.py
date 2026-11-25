"""附件 MolParser HTTP 协议的异步适配；E-SMILES 原文保持不变。

连接参数由 Services 从根目录 .env 注入。分子 Skill 与事实回填仍属于 P4。
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import time
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import httpx2


class MolParserError(RuntimeError):
    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class MolParserClient:
    def __init__(
        self,
        base_url: str,
        *,
        timeout_s: float = 60,
        rec_only: bool = True,
        http_client: httpx2.AsyncClient | None = None,
        backoff_s: float = 0.25,
        queue_wait_s: float = 60,
    ) -> None:
        if not base_url.strip():
            raise ValueError("请先配置 MOLPARSER_BASE_URL")
        self.base_url = base_url.strip().rstrip("/")
        self.rec_only = rec_only
        self.timeout_s = timeout_s
        self._http = http_client or httpx2.AsyncClient(timeout=timeout_s)
        self.backoff_s, self.queue_wait_s = backoff_s, queue_wait_s

    async def aclose(self) -> None:
        await self._http.aclose()

    async def _request(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        for attempt in range(3):
            try:
                return await self._request_once(method, path, **kwargs)
            except MolParserError as exc:
                if attempt == 2 or (exc.status_code is not None and exc.status_code < 500):
                    raise
                await asyncio.sleep(self.backoff_s * 2**attempt)
        raise AssertionError("unreachable")

    async def _request_once(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        try:
            response = await self._http.request(
                method, self.base_url + path, timeout=self.timeout_s, **kwargs
            )
        except httpx2.HTTPError as exc:
            raise MolParserError(f"{method} {path} 请求失败：{exc}") from exc
        if not 200 <= response.status_code < 300:
            raise MolParserError(
                f"{method} {path} -> HTTP {response.status_code}: {response.text[:200]}",
                status_code=response.status_code,
            )
        try:
            payload = response.json()
        except ValueError as exc:
            raise MolParserError(f"{method} {path} 返回非 JSON 数据") from exc
        if not isinstance(payload, dict):
            raise MolParserError(f"{method} {path} 返回的 JSON 不是对象")
        return payload

    @staticmethod
    def _results(payload: dict[str, Any]) -> list[dict[str, Any]]:
        results = payload.get("results")
        if not isinstance(results, list) or not all(isinstance(item, dict) for item in results):
            raise MolParserError("识别响应缺少合法 results 列表")
        return results

    async def health(self) -> dict[str, Any]:
        return await self._request("GET", "/health")

    async def recognize_files(
        self,
        paths_or_names: Sequence[str | Path],
        *,
        blobs: Sequence[bytes] | None = None,
        rec_only: bool | None = None,
    ) -> list[dict[str, Any]]:
        if blobs is not None and len(blobs) != len(paths_or_names):
            raise ValueError("文件名与 blobs 数量不一致")
        if not paths_or_names:
            return []
        files = [
            (
                "files",
                (
                    Path(name).name,
                    blobs[i] if blobs is not None else await asyncio.to_thread(Path(name).read_bytes),
                    "application/octet-stream",
                ),
            )
            for i, name in enumerate(paths_or_names)
        ]
        mode = self.rec_only if rec_only is None else rec_only
        return self._results(
            await self._request("POST", "/recognize", files=files, data={"rec_only": str(mode).lower()})
        )

    async def recognize_json(
        self, items: Sequence[str | dict[str, Any]], *, rec_only: bool | None = None
    ) -> list[dict[str, Any]]:
        if not items:
            return []
        mode = self.rec_only if rec_only is None else rec_only
        return self._results(
            await self._request("POST", "/recognize/json", json={"images": list(items), "rec_only": mode})
        )

    async def _wait_capacity(self) -> None:
        deadline = time.monotonic() + self.queue_wait_s
        while True:
            state = await self.health()
            size, maximum = state.get("queue_size", 0), state.get("queue_maxsize", 0)
            if not maximum or size / maximum <= 0.8:
                return
            if time.monotonic() >= deadline:
                raise MolParserError("识别队列背压等待超时", status_code=503)
            await asyncio.sleep(min(0.25, max(0, deadline - time.monotonic())))

    @staticmethod
    def _align(results: Sequence[dict[str, Any]], count: int) -> dict[int, dict[str, Any]]:
        aligned: dict[int, dict[str, Any]] = {}
        for result in results:
            index = result.get("input_index")
            if type(index) is not int or not 0 <= index < count or index in aligned:
                raise MolParserError("识别结果 input_index 无效或重复", status_code=422)
            aligned[index] = result
        return aligned

    async def _image_bytes(self, value: bytes | str | Path) -> bytes:
        if isinstance(value, bytes):
            return value
        if (
            isinstance(value, Path)
            or str(value).lower().endswith((".png", ".jpg", ".jpeg", ".webp", ".tif"))
            and not str(value).startswith(("https://", "http://"))
        ):
            return await asyncio.to_thread(Path(value).read_bytes)
        if str(value).startswith(("http://", "https://")):
            response = await self._http.get(str(value), timeout=self.timeout_s)
            response.raise_for_status()
            return response.content
        try:
            return base64.b64decode(str(value).split(",", 1)[-1], validate=True)
        except (binascii.Error, ValueError) as exc:
            raise ValueError("输入不是有效文件、URL 或 base64") from exc

    async def recognize(
        self, images: Sequence[bytes | str | Path], *, rec_only: bool = True
    ) -> list[dict[str, Any]]:
        """全部输入保留位置；混合来源统一 multipart，缺失单张仅补救一次。"""
        output: list[dict[str, Any]] = [
            {"input_index": i, "error": "missing_result"} for i in range(len(images))
        ]
        valid: list[tuple[int, bytes]] = []
        for i, image in enumerate(images):
            try:
                valid.append((i, await self._image_bytes(image)))
            except (OSError, ValueError, httpx2.HTTPError) as exc:
                output[i] = {"input_index": i, "error": str(exc)}
        for start in range(0, len(valid), 32):
            batch = valid[start : start + 32]
            await self._wait_capacity()
            service_records = await self.recognize_files(
                [f"image_{i}.png" for i, _ in batch], blobs=[blob for _, blob in batch], rec_only=rec_only
            )
            try:
                records = self._align(service_records, len(batch))
            except MolParserError:
                # 合并批次全局索引不可用于绑定；丢弃映射，逐张独立补救。
                records = {}
            for local, (original, blob) in enumerate(batch):
                record = records.get(local)
                if (
                    record is None
                    or record.get("error")
                    or not any(
                        isinstance(record.get(k), str) and record[k].strip()
                        for k in ("esmi", "caption", "raw_caption")
                    )
                ):
                    await self._wait_capacity()
                    single_results = await self.recognize_files(
                        [f"image_{original}.png"], blobs=[blob], rec_only=rec_only
                    )
                    # 单输入单输出的对应关系由请求基数确定，保留异常服务索引供追踪。
                    if (
                        len(single_results) == 1
                        and type(single_results[0].get("input_index")) is int
                        and single_results[0]["input_index"] >= 0
                    ):
                        service_index = single_results[0]["input_index"]
                        single_results = [
                            {**single_results[0], "input_index": 0, "service_input_index": service_index}
                        ]
                    retry = self._align(single_results, 1)
                    record = retry.get(0, {"error": "missing_result"})
                if not record.get("error") and not any(
                    isinstance(record.get(k), str) and record[k].strip()
                    for k in ("esmi", "caption", "raw_caption")
                ):
                    record = {**record, "error": "empty_esmiles"}
                output[original] = {**record, "input_index": original}
        return output


def extract_esmiles(results: Sequence[Mapping[str, Any]]) -> list[str]:
    values = []
    for result in results:
        for field in ("esmi", "caption", "raw_caption"):
            value = result.get(field)
            if isinstance(value, str) and value.strip():
                values.append(value)
                break
        else:
            raise ValueError("MolParser 结果没有非空的 esmi / caption / raw_caption")
    return values
