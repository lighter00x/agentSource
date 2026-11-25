"""模拟 OpenAI 兼容端点的 HTTP 层。

openai>=3 的底层 HTTP 库是 httpx2（respx 只能拦截 httpx），因此这里用 httpx2.MockTransport
实现同等能力：按顺序返回预设响应或抛出异常，并记录每次收到的请求。
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

import httpx2

Item = httpx2.Response | Exception | Callable[[httpx2.Request], httpx2.Response | Exception]


class MockChatServer:
    def __init__(self, *items: Item, repeat_last: bool = True) -> None:
        self._items = list(items)
        self._repeat_last = repeat_last
        self.requests: list[httpx2.Request] = []

    async def _handle(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        if not self._items:
            raise AssertionError("MockChatServer：没有更多预设响应")
        item = self._items[0] if (len(self._items) == 1 and self._repeat_last) else self._items.pop(0)
        if callable(item) and not isinstance(item, httpx2.Response | Exception):
            item = item(request)
        if isinstance(item, httpx2.TimeoutException):
            raise type(item)(str(item), request=request)
        if isinstance(item, Exception):
            raise item
        return item

    def http_client(self) -> httpx2.AsyncClient:
        return httpx2.AsyncClient(transport=httpx2.MockTransport(self._handle))

    @property
    def call_count(self) -> int:
        return len(self.requests)

    def body(self, i: int) -> dict[str, Any]:
        return json.loads(self.requests[i].content)
