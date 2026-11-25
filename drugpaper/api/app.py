"""FastAPI 应用（执行方案 S2.13）。前缀 `/api/v1`。

安全提示：P2 的 API **没有鉴权与用户隔离**（P7 补上），只应在本机或受信任的内网中运行。
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI

from drugpaper import __version__
from drugpaper.api import (
    batches,
    blobs,
    compounds,
    documents,
    figures,
    full_text,
    monitoring,
    nodes,
    search,
    skills,
    workbench,
)
from drugpaper.api.errors import install_error_handlers
from drugpaper.services import Services

API_PREFIX = "/api/v1"


def create_app(services: Services | None = None) -> FastAPI:
    owns = services is None

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        sv = app.state.services if getattr(app.state, "services", None) else Services.from_settings()
        app.state.services = sv
        if not sv.db.is_postgres:
            await sv.db.create_all()  # 本地 SQLite 免迁移；PostgreSQL 请先 `drugpaper db upgrade`
        bundle = await sv.skills()
        stop = asyncio.Event()
        tasks = []
        if sv.settings.skills_watch_enabled:
            tasks.append(asyncio.create_task(bundle.watcher.watch(stop)))
        if sv.settings.redis_url:
            from redis.asyncio import Redis

            bundle.watcher.redis = Redis.from_url(sv.settings.redis_url)
            tasks.append(asyncio.create_task(bundle.watcher.subscribe(stop)))
        try:
            yield
        finally:
            stop.set()
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            if bundle.watcher.redis:
                await bundle.watcher.redis.aclose()
            if owns:
                await sv.aclose()

    app = FastAPI(
        title="drugpaper API",
        version=__version__,
        description="药物论文理解 Agent：文档、节点、Skill、化合物、图像 Agent 与批量队列（P5）。本阶段没有鉴权，只应在受信任网络中运行。",
        lifespan=lifespan,
    )
    if services is not None:
        app.state.services = services
    install_error_handlers(app)
    for r in (
        documents.router,
        nodes.router,
        search.router,
        blobs.router,
        skills.router,
        compounds.router,
        figures.router,
        batches.router,
        full_text.router,
        monitoring.router,
        workbench.router,
    ):
        app.include_router(r, prefix=API_PREFIX)

    @app.get("/healthz", tags=["meta"])
    async def healthz() -> dict[str, Any]:
        return {"status": "ok", "version": __version__}

    return app


def export_openapi(path: Path | str) -> dict[str, Any]:
    spec = create_app(services=None).openapi()
    Path(path).write_text(json.dumps(spec, ensure_ascii=False, indent=2), encoding="utf-8")
    return spec
