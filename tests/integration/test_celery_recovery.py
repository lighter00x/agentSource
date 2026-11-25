import asyncio
import json
import os
import subprocess
import sys
import uuid

import pytest
from redis.asyncio import Redis

from drugpaper.config.settings import Settings
from drugpaper.domain.geometry import BBox, Geometry, Region
from drugpaper.domain.nodes import DocumentNode, IRDocument, PageInfo
from drugpaper.persistence.migrate import upgrade_async
from drugpaper.persistence.models import Document
from drugpaper.persistence.repos.nodes import save_ir
from drugpaper.services import Services
from drugpaper.workers.celery_app import create_celery, redis_db

pytestmark = pytest.mark.integration


async def test_hard_killed_celery_worker_redelivers_and_resumes(pg_url, tmp_path):
    import pymupdf

    base = Settings()
    if not base.redis_url:
        pytest.skip("Redis not configured")
    red = Redis.from_url(redis_db(base.redis_url, 14))
    await red.ping()
    await upgrade_async(pg_url)
    cfg = Settings(
        _env_file=None,
        database_url=pg_url,
        blob_root=tmp_path / "blobs",
        skills_root=tmp_path / "skills",
        redis_url=redis_db(base.redis_url, 14),
        celery_broker_url=redis_db(base.redis_url, 14),
        celery_result_backend=redis_db(base.redis_url, 14),
        skills_watch_enabled=False,
    )
    sv = Services.from_settings(cfg)
    pdf = pymupdf.open()
    pdf.new_page(width=100, height=100)
    sha = await sv.store.put(pdf.tobytes())
    pdf.close()
    async with sv.db.session() as session:
        session.add(
            Document(id="doc_worker", user_id="default", sha256=sha, status="ready", active_revision=1)
        )
        await session.flush()
        await save_ir(
            session,
            IRDocument(
                document_id="doc_worker",
                pages=[PageInfo(page=0, width_pt=100, height_pt=100)],
                nodes=[
                    DocumentNode(
                        document_id="doc_worker",
                        revision=1,
                        local_id="p000.g000",
                        node_type="table",
                        geometry=Geometry(regions=[Region(page=0, bbox=BBox(x0=0, y0=0, x1=1, y1=1))]),
                    )
                ],
            ),
        )
    await sv.aclose()
    key = "dp-test-worker-" + uuid.uuid4().hex
    queue = key
    path = tmp_path / "worker.json"
    path.write_text(
        json.dumps(
            {"settings": cfg.model_dump(mode="json"), "node_ref": "doc_worker/r1/p000.g000", "key": key}
        )
    )
    path.chmod(0o600)
    env = {**os.environ, "DP_VERIFY_WORKER_CONFIG": str(path)}
    logfile = (tmp_path / "worker.log").open("w")
    command = [
        sys.executable,
        "-m",
        "celery",
        "-A",
        "tests.integration.figure_worker_app:app",
        "worker",
        "--pool=solo",
        "--concurrency=1",
        "--queues=" + queue,
        "--hostname=" + key + "@%h",
        "--loglevel=WARNING",
        "--without-gossip",
        "--without-mingle",
        "--without-heartbeat",
    ]
    app = create_celery(cfg)
    worker = None

    async def wait_for(predicate):
        async with asyncio.timeout(70):
            while not await predicate():  # noqa: ASYNC110 - poll state owned by another process
                await asyncio.sleep(0.2)

    try:
        worker = await asyncio.to_thread(
            subprocess.Popen, command, env=env, stdout=logfile, stderr=subprocess.STDOUT
        )
        result = app.send_task("drugpaper.verify_checkpoint", queue=queue)
        await wait_for(lambda: red.exists(key + ":paused"))
        assert int(await red.get(key + ":calls")) == 1
        worker.kill()
        await asyncio.to_thread(worker.wait, timeout=10)
        await red.set(key + ":continue", "1")
        worker = await asyncio.to_thread(
            subprocess.Popen, command, env=env, stdout=logfile, stderr=subprocess.STDOUT
        )

        async def ready():
            return await asyncio.to_thread(result.ready)

        await wait_for(ready)
        data = await asyncio.to_thread(result.get, timeout=1)
        assert data["status"] == "ok", data
        assert data["calls"] == 3 and int(await red.get(key + ":calls")) == 3
        await asyncio.to_thread(result.forget)
    finally:
        if worker and worker.poll() is None:
            worker.terminate()
            try:
                await asyncio.to_thread(worker.wait, timeout=10)
            except subprocess.TimeoutExpired:
                worker.kill()
                await asyncio.to_thread(worker.wait, timeout=10)
        logfile.close()
        await red.delete(key + ":paused", key + ":continue", key + ":calls", queue)
        await red.aclose()
        app.close()
