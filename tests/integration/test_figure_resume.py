import asyncio
import uuid

import pytest
from redis.asyncio import Redis

from drugpaper.agent.checkpoint import open_checkpointer
from drugpaper.agents.figure.graph import FigureAgent
from drugpaper.config.settings import Settings
from drugpaper.domain.geometry import BBox, Geometry, Region
from drugpaper.domain.nodes import DocumentNode, IRDocument, PageInfo
from drugpaper.gateway.limiter import EndpointLimiter
from drugpaper.gateway.task_router import TaskRouter
from drugpaper.persistence.migrate import upgrade_async
from drugpaper.persistence.models import Document
from drugpaper.persistence.repos.nodes import save_ir
from drugpaper.services import Services
from tests.unit.figure.test_agent import FigureFake

pytestmark = pytest.mark.integration


async def test_figure_resume_after_process_interruption(pg_url, tmp_path):
    import pymupdf

    await upgrade_async(pg_url)
    sv = Services.from_settings(
        Settings(
            _env_file=None,
            database_url=pg_url,
            blob_root=tmp_path / "blobs",
            skills_root=tmp_path / "skills",
            redis_url="",
            skills_watch_enabled=False,
        )
    )
    await sv.db.create_all()
    from drugpaper.detection.service import MoleculeDetectionService
    from tests.unit.detection.test_service import FakeBackend

    sv._detector = MoleculeDetectionService(FakeBackend(boxes=[]), sv.settings)

    pdf = pymupdf.open()
    pdf.new_page(width=100, height=100)
    sha = await sv.store.put(pdf.tobytes())
    pdf.close()
    async with sv.db.session() as session:
        session.add(
            Document(id="doc_resume", user_id="default", sha256=sha, status="ready", active_revision=1)
        )
        await session.flush()
        await save_ir(
            session,
            IRDocument(
                document_id="doc_resume",
                pages=[PageInfo(page=0, width_pt=100, height_pt=100)],
                nodes=[
                    DocumentNode(
                        document_id="doc_resume",
                        revision=1,
                        local_id="p000.g000",
                        node_type="table",
                        geometry=Geometry(regions=[Region(page=0, bbox=BBox(x0=0, y0=0, x1=1, y1=1))]),
                    )
                ],
            ),
        )
    bundle = await sv.skills()

    class Interrupted(FigureFake):
        async def chat(self, req, *, task="default"):
            if task == "figure_structure":
                raise asyncio.CancelledError("worker lost")
            return await super().chat(req, task=task)

    first = Interrupted()
    first.router = TaskRouter.from_file(sv.settings.models_config_path)
    bundle.models = first
    try:
        async with open_checkpointer(pg_url) as saver:
            try:
                unexpected = await FigureAgent(sv, saver).run("doc_resume/r1/p000.g000")
            except asyncio.CancelledError:
                pass
            else:
                pytest.fail(str(unexpected.open_questions))
        fresh = FigureFake()
        fresh.router = first.router
        bundle.models = fresh
        # A fresh graph/checkpointer connection models process restart.
        async with open_checkpointer(pg_url) as saver:
            result = await FigureAgent(sv, saver).run("doc_resume/r1/p000.g000")
        assert result.status == "ok", result.open_questions
        assert [t for t, _ in first.calls] == ["visual_classify"]
        assert [t for t, _ in fresh.calls] == ["figure_structure", "figure_self_check"]
        assert result.accounting.fixed_calls == 3
    finally:
        await sv.aclose()


async def test_redis_limits_multiple_clients_and_releases_on_cancellation():
    from drugpaper.workers.celery_app import redis_db

    settings = Settings()
    if not settings.redis_url:
        pytest.skip("Redis not configured")
    clients = [Redis.from_url(redis_db(settings.redis_url, 14)) for _ in range(2)]
    try:
        await clients[0].ping()
    except Exception:
        await asyncio.gather(*(c.aclose() for c in clients))
        pytest.skip("local deployed Redis unavailable")
    endpoint = "test-" + uuid.uuid4().hex
    limiters = [EndpointLimiter(c, limit=2, lease_s=0.3, wait_s=3) for c in clients]
    active = peak = 0

    async def work(i):
        nonlocal active, peak
        async with limiters[i % 2].slot(endpoint):
            active += 1
            peak = max(peak, active)
            await asyncio.sleep(0.12)
            active -= 1

    try:
        await asyncio.gather(*(work(i) for i in range(8)))
        assert peak == 2
        entered = asyncio.Event()

        async def cancelled():
            async with limiters[0].slot(endpoint):
                entered.set()
                await asyncio.Event().wait()

        task = asyncio.create_task(cancelled())
        await entered.wait()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        active = peak = 0
        await asyncio.gather(work(0), work(1))
        assert peak == 2
    finally:
        await asyncio.gather(*(limiter.aclose() for limiter in limiters))


async def test_parallel_checkpoint_initialization_is_safe(pg_url):
    await upgrade_async(pg_url)

    async def open_once(i):
        async with open_checkpointer(pg_url) as saver:
            assert await saver.aget_tuple({"configurable": {"thread_id": f"init-{i}"}}) is None

    await asyncio.gather(*(open_once(i) for i in range(8)))
