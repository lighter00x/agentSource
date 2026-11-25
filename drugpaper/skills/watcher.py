"""文件热加载和 Redis 通知；内容寻址快照隔离进行中的 Run。"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from watchfiles import awatch

from drugpaper.capabilities.assembler import PROMPTS_ROOT
from drugpaper.skills.protocol import Scope
from drugpaper.skills.registry import SkillRegistry


class SkillWatcher:
    def __init__(
        self,
        registry: SkillRegistry,
        roots: list[tuple[Path, Scope]],
        *,
        redis: Any = None,
        on_change: Callable[[], Awaitable[None]] | None = None,
    ) -> None:
        self.registry, self.roots, self.redis, self.on_change = registry, roots, redis, on_change

    async def reload(self, *, publish: bool = True) -> None:
        before = self.registry.version
        await self.registry.scan(self.roots)
        if self.on_change:
            await self.on_change()
        if publish and self.redis and self.registry.version != before:
            records = [
                {"name": s.name, "version": s.version, "hash": s.content_hash}
                for s in await self.registry.list_visible()
            ]
            await self.redis.publish("skills:changed", json.dumps(records))

    async def watch(self, stop: asyncio.Event) -> None:
        for root, _ in self.roots:
            root.mkdir(parents=True, exist_ok=True)
        await self.reload()
        async for _changes in awatch(
            *(p for p, _ in self.roots), PROMPTS_ROOT / "common", stop_event=stop, debounce=100, step=50
        ):
            await self.reload()

    async def subscribe(self, stop: asyncio.Event) -> None:
        if not self.redis:
            return
        async with self.redis.pubsub() as pubsub:
            await pubsub.subscribe("skills:changed")
            while not stop.is_set():
                message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=0.5)
                if message:
                    self.registry.version += 1
                    self.registry._packages.clear()
                    if self.on_change:
                        await self.on_change()
