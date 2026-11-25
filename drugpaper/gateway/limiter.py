"""Endpoint-wide leased Redis semaphore, with a process-local offline fallback."""

from __future__ import annotations

import asyncio
import hashlib
import time
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

ACQUIRE = """
local now=tonumber(ARGV[1])
redis.call('ZREMRANGEBYSCORE',KEYS[1],'-inf',now)
if redis.call('ZCARD',KEYS[1]) < tonumber(ARGV[2]) then
 redis.call('ZADD',KEYS[1],now+tonumber(ARGV[3]),ARGV[4])
 redis.call('EXPIRE',KEYS[1],math.ceil(tonumber(ARGV[3])/1000)+1)
 return 1
end
return 0
"""


class EndpointLimiter:
    def __init__(self, redis: Any = None, limit: int = 8, lease_s: float = 600, wait_s: float = 120):
        if limit < 1 or lease_s <= 0 or wait_s <= 0:
            raise ValueError("invalid endpoint limiter limits")
        self.redis = redis
        self.limit = limit
        self.lease_s = lease_s
        self.wait_s = wait_s
        self.local: dict[str, asyncio.Semaphore] = {}

    @asynccontextmanager
    async def slot(self, endpoint: str) -> AsyncIterator[None]:
        key = "drugpaper:model:slots:" + hashlib.sha256(endpoint.rstrip("/").encode()).hexdigest()
        token = uuid.uuid4().hex
        if self.redis is None:
            semaphore = self.local.setdefault(key, asyncio.Semaphore(self.limit))
            async with asyncio.timeout(self.wait_s):
                await semaphore.acquire()
            try:
                yield
            finally:
                semaphore.release()
            return
        start = time.monotonic()
        while not await self.redis.eval(
            ACQUIRE, 1, key, int(time.time() * 1000), self.limit, int(self.lease_s * 1000), token
        ):
            if time.monotonic() - start > self.wait_s:
                raise TimeoutError("endpoint concurrency wait exceeded")
            await asyncio.sleep(0.05)

        async def renew() -> None:
            while True:
                await asyncio.sleep(self.lease_s / 3)
                await self.redis.zadd(key, {token: int((time.time() + self.lease_s) * 1000)}, xx=True)

        task = asyncio.create_task(renew())
        try:
            yield
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            await asyncio.shield(self.redis.zrem(key, token))

    async def aclose(self) -> None:
        if self.redis:
            await self.redis.aclose()
