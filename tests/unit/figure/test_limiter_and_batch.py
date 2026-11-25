import asyncio

import pytest

from drugpaper.config.settings import Settings
from drugpaper.gateway.limiter import EndpointLimiter
from drugpaper.workers.celery_app import create_celery, redis_db


async def test_endpoint_limit_shared_cancellation_and_independent_endpoints():
    limiter = EndpointLimiter(limit=2)
    active = 0
    peak = 0

    async def call():
        nonlocal active, peak
        async with limiter.slot("http://endpoint"):
            active += 1
            peak = max(peak, active)
            await asyncio.sleep(0.01)
            active -= 1

    await asyncio.gather(*(call() for _ in range(8)))
    assert peak == 2
    entered = asyncio.Event()

    async def cancellable():
        async with limiter.slot("http://endpoint"):
            entered.set()
            await asyncio.Event().wait()

    task = asyncio.create_task(cancellable())
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    async with limiter.slot("http://endpoint"):
        pass
    assert limiter.local[next(iter(limiter.local))]._value == 2


def test_celery_late_ack_separate_queues_and_visibility():
    app = create_celery(Settings(_env_file=None, redis_url="redis://localhost:6379/10"))
    assert app.conf.task_acks_late and app.conf.task_reject_on_worker_lost
    assert app.conf.worker_prefetch_multiplier == 1
    assert app.conf.broker_url.endswith("/11") and app.conf.result_backend.endswith("/12")
    assert app.conf.broker_transport_options["visibility_timeout"] >= 86400
    assert redis_db("redis://localhost:6379/10", 12).endswith("/12")
