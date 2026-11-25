"""legacy_mineru_merge 服务的异步客户端（执行方案 S2.2）。

接口（服务端 app.py）：
    POST /api/v1/tasks                  multipart `file=`，返回 {task_id}；队列满时 429
    GET  /api/v1/tasks/{id}             {status: pending/running/succeeded/failed, result, error}
    GET  /api/v1/tasks/{id}/result      {files: [{path, content | content_base64}]}（服务端可能"取回即删"）

客户端侧用 `asyncio.Semaphore(max_inflight)` 控制并发（低于服务端 MAX_QUEUE=10），收到 429 时指数退避重试。
"""

from __future__ import annotations

import asyncio
import base64
import time
from typing import Any

import httpx2
import orjson
from tenacity import AsyncRetrying, retry_if_exception, stop_after_attempt, wait_exponential

from drugpaper.parsers.mineru_legacy.paths import safe_rel_path


class MinerUError(Exception):
    pass


class MinerUQueueFull(MinerUError):
    pass


class MinerUTaskFailed(MinerUError):
    pass


class MinerUTimeout(MinerUError):
    pass


def _retryable(e: BaseException) -> bool:
    return isinstance(e, MinerUQueueFull | httpx2.TransportError)


class MinerUClient:
    def __init__(
        self,
        base_url: str,
        *,
        max_inflight: int = 4,
        poll_interval_s: float = 5.0,
        timeout_s: float = 1800.0,
        submit_attempts: int = 6,
        backoff_s: float = 2.0,
        submit_timeout_s: float = 60.0,
        status_timeout_s: float = 30.0,
        result_timeout_s: float = 120.0,
        http_client: httpx2.AsyncClient | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self._sem = asyncio.Semaphore(max_inflight)
        self.poll_interval_s = poll_interval_s
        self.timeout_s = timeout_s
        self._submit_attempts = submit_attempts
        self._backoff_s = backoff_s
        self.submit_timeout_s = submit_timeout_s
        self.status_timeout_s = status_timeout_s
        self.result_timeout_s = result_timeout_s
        self._http = http_client or httpx2.AsyncClient(timeout=httpx2.Timeout(120.0))

    async def aclose(self) -> None:
        await self._http.aclose()

    # ------------------------------------------------------------ 基础接口

    async def submit(self, pdf_bytes: bytes, filename: str) -> str:
        if not pdf_bytes.startswith(b"%PDF-"):
            raise MinerUError("不是有效的 PDF（魔数校验失败）")
        async for attempt in AsyncRetrying(
            stop=stop_after_attempt(self._submit_attempts),
            wait=wait_exponential(multiplier=self._backoff_s, max=60),
            retry=retry_if_exception(_retryable),
            reraise=True,
        ):
            with attempt:
                resp = await self._http.post(
                    f"{self.base_url}/api/v1/tasks",
                    files={"file": (filename, pdf_bytes, "application/pdf")},
                    timeout=self.submit_timeout_s,
                )
                if resp.status_code == 429:
                    raise MinerUQueueFull(resp.text)
                if resp.status_code not in (200, 202):
                    raise MinerUError(f"提交失败 [{resp.status_code}]：{resp.text[:200]}")
                task_id = resp.json().get("task_id")
                if not task_id:
                    raise MinerUError("提交响应中没有 task_id")
                return str(task_id)
        raise MinerUError("提交失败")  # pragma: no cover

    async def status(self, task_id: str) -> dict[str, Any]:
        resp = await self._http.get(f"{self.base_url}/api/v1/tasks/{task_id}", timeout=self.status_timeout_s)
        if resp.status_code != 200:
            raise MinerUError(f"查询任务失败 [{resp.status_code}]：{resp.text[:200]}")
        data: dict[str, Any] = resp.json()
        return data

    async def wait(self, task_id: str) -> dict[str, Any]:
        deadline = time.monotonic() + self.timeout_s
        while True:
            data = await self.status(task_id)
            st = data.get("status")
            if st == "succeeded":
                return data
            if st in ("failed", "cancelled"):
                raise MinerUTaskFailed(f"任务 {task_id} 失败：{data.get('error')}")
            if time.monotonic() >= deadline:
                raise MinerUTimeout(f"任务 {task_id} 在 {self.timeout_s:.0f}s 内未完成")
            await asyncio.sleep(self.poll_interval_s)

    async def fetch(self, task_id: str) -> dict[str, bytes]:
        """返回 {相对路径: 内容}。路径做穿越过滤；JSON 内容重新序列化为字节。"""
        resp = await self._http.get(
            f"{self.base_url}/api/v1/tasks/{task_id}/result", timeout=self.result_timeout_s
        )
        if resp.status_code != 200:
            raise MinerUError(f"获取结果失败 [{resp.status_code}]：{resp.text[:200]}")
        files: dict[str, bytes] = {}
        for f in resp.json().get("files", []):
            rel = safe_rel_path(str(f.get("path", "")))
            if rel is None:
                continue
            if "content_base64" in f:
                files[rel] = base64.b64decode(f["content_base64"])
            elif "content" in f:
                c = f["content"]
                files[rel] = orjson.dumps(c) if isinstance(c, dict | list) else str(c).encode("utf-8")
            else:
                dl = await self._http.get(
                    f"{self.base_url}/api/v1/tasks/{task_id}/download/{rel}", timeout=self.result_timeout_s
                )
                if dl.status_code != 200:
                    raise MinerUError(f"下载 {rel} 失败 [{dl.status_code}]")
                files[rel] = dl.content
        return files

    # ------------------------------------------------------------ 组合

    async def parse(self, pdf_bytes: bytes, filename: str) -> dict[str, bytes]:
        """提交 → 等待 → 拉取。并发受 max_inflight 限制（客户端侧背压）。"""
        async with self._sem:
            task_id = await self.submit(pdf_bytes, filename)
            await self.wait(task_id)
            return await self.fetch(task_id)
