"""Isolated Celery recovery harness; uses FakeVLM and test-only Redis keys."""

import asyncio
import json
import os
from pathlib import Path

from redis.asyncio import Redis

from drugpaper.agent.checkpoint import open_checkpointer
from drugpaper.agents.figure.graph import FigureAgent
from drugpaper.config.settings import Settings
from drugpaper.gateway.task_router import TaskRouter
from drugpaper.services import Services
from drugpaper.workers.celery_app import create_celery
from tests.unit.figure.test_agent import FigureFake

config = json.loads(Path(os.environ["DP_VERIFY_WORKER_CONFIG"]).read_text())
settings = Settings(_env_file=None, **config["settings"])
app = create_celery(settings)
app.conf.broker_transport_options = {"visibility_timeout": 3, "polling_interval": 0.1}
app.conf.result_backend_transport_options = {"visibility_timeout": 3}
app.conf.visibility_timeout = 3


@app.task(name="drugpaper.verify_checkpoint", acks_late=True, reject_on_worker_lost=True)
def verify_checkpoint():
    async def execute():
        red = Redis.from_url(settings.redis_url)
        sv = Services.from_settings(settings)
        from drugpaper.detection.service import MoleculeDetectionService
        from tests.unit.detection.test_service import FakeBackend

        sv._detector = MoleculeDetectionService(FakeBackend(boxes=[]), sv.settings)

        class CountedFake(FigureFake):
            async def chat(self, req, *, task="default"):
                await red.incr(config["key"] + ":calls")
                return await super().chat(req, task=task)

        class PausedAgent(FigureAgent):
            async def _prepare(self, state):
                await red.set(config["key"] + ":paused", "1")
                while not await red.exists(config["key"] + ":continue"):  # noqa: ASYNC110 - distributed restart barrier
                    await asyncio.sleep(0.1)
                return await super()._prepare(state)

        fake = CountedFake()
        fake.router = TaskRouter.from_file(settings.models_config_path)
        bundle = await sv.skills()
        bundle.models = fake
        try:
            async with open_checkpointer(settings.database_url) as saver:
                report = await PausedAgent(sv, saver).run(config["node_ref"])
            return {
                "status": report.status,
                "calls": report.accounting.total,
                "questions": report.open_questions,
            }
        finally:
            await sv.aclose()
            await red.aclose()

    return asyncio.run(execute())
