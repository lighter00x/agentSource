"""MinerU 客户端（模拟服务）与离线导入。执行方案把在线客户端测试放在 integration，这里用 MockTransport，不访问外部服务。"""

from __future__ import annotations

import base64
import json
from pathlib import Path

import httpx2
import pytest

from drugpaper.parsers.mineru_legacy.client import MinerUClient, MinerUError, MinerUTaskFailed, MinerUTimeout
from drugpaper.parsers.mineru_legacy.importer import read_output_dir, source_name_of, store_result
from drugpaper.parsers.mineru_legacy.paths import asset_kind, safe_rel_path
from drugpaper.persistence.db import Database
from drugpaper.persistence.models import Document
from drugpaper.persistence.repos.documents import create_document, get_revision
from drugpaper.storage.assets import list_assets
from drugpaper.storage.localfs import LocalFsBlobStore
from tests.unit.ir.conftest import FIXTURE_DIR, needs_real

PDF = b"%PDF-1.7 fake"


class FakeMinerU:
    """按 legacy_mineru_merge/app.py 的接口行为模拟服务端。"""

    def __init__(self, *, queue_full_times: int = 0, polls_before_done: int = 1, fail: bool = False) -> None:
        self.queue_full_times = queue_full_times
        self.polls_before_done = polls_before_done
        self.fail = fail
        self.polls = 0
        self.submits = 0
        self.inflight = 0
        self.max_inflight = 0

    def handler(self, req: httpx2.Request) -> httpx2.Response:
        path = req.url.path
        if req.method == "POST" and path == "/api/v1/tasks":
            self.submits += 1
            if self.queue_full_times > 0:
                self.queue_full_times -= 1
                return httpx2.Response(429, json={"detail": "queue full"})
            assert b'name="file"' in req.content and PDF in req.content
            self.inflight += 1
            self.max_inflight = max(self.max_inflight, self.inflight)
            return httpx2.Response(202, json={"task_id": f"t{self.submits}", "status": "pending"})
        if req.method == "GET" and path.endswith("/result"):
            self.inflight -= 1
            files = [
                {
                    "path": "x_layout.json",
                    "content": [{"page_idx": 0, "page_size": [612, 792], "para_blocks": []}],
                },
                {"path": "x.md", "content": "# md"},
                {"path": "images/a.jpg", "content_base64": base64.b64encode(b"\xff\xd8\xffJPEG").decode()},
                {"path": "../evil.txt", "content": "nope"},
                {"path": "/abs.txt", "content": "nope"},
            ]
            return httpx2.Response(200, json={"task_id": "t", "status": "succeeded", "files": files})
        if req.method == "GET" and path.startswith("/api/v1/tasks/"):
            self.polls += 1
            if self.fail:
                return httpx2.Response(200, json={"status": "failed", "error": "boom"})
            done = self.polls >= self.polls_before_done
            return httpx2.Response(
                200, json={"status": "succeeded" if done else "running", "result": {"doc_out": "/x"}}
            )
        return httpx2.Response(404)


def client(server: FakeMinerU, **kw: object) -> MinerUClient:
    return MinerUClient(
        "http://mineru.test",
        poll_interval_s=0,
        backoff_s=0,
        http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(server.handler)),
        **kw,  # type: ignore[arg-type]
    )


def test_safe_rel_path_and_kinds() -> None:
    assert safe_rel_path("images/a.jpg") == "images/a.jpg"
    assert safe_rel_path("a\\.\\b.json") == "a/b.json"
    for bad in ["../x", "a/../../x", "/etc/passwd", "C:\\x", "", "."]:
        assert safe_rel_path(bad) is None
    assert asset_kind("doc_layout.json") == "raw_layout_json"
    assert asset_kind("doc_middle.json") == "raw_middle_json"
    assert asset_kind("merged_images/x.jpg") == "merged_image"
    assert asset_kind("doc_origin.pdf") == "origin_pdf"
    assert asset_kind("debug_cross_page/x.png") == "debug"


async def test_parse_end_to_end_with_path_filter() -> None:
    server = FakeMinerU(polls_before_done=3)
    files = await client(server).parse(PDF, "x.pdf")
    assert set(files) == {"x_layout.json", "x.md", "images/a.jpg"}
    assert json.loads(files["x_layout.json"])[0]["page_idx"] == 0
    assert files["images/a.jpg"].startswith(b"\xff\xd8")
    assert server.polls == 3


async def test_queue_full_backoff() -> None:
    server = FakeMinerU(queue_full_times=2)
    task = await client(server).submit(PDF, "x.pdf")
    assert task == "t3" and server.submits == 3


async def test_failures() -> None:
    with pytest.raises(MinerUTaskFailed):
        await client(FakeMinerU(fail=True)).parse(PDF, "x.pdf")
    with pytest.raises(MinerUTimeout):
        await client(FakeMinerU(polls_before_done=10**9), timeout_s=0).wait("t1")
    with pytest.raises(MinerUError):
        await client(FakeMinerU()).submit(b"not a pdf", "x.pdf")


async def test_client_side_backpressure() -> None:
    import asyncio

    server = FakeMinerU(polls_before_done=2)
    c = client(server, max_inflight=2)
    await asyncio.gather(*(c.parse(PDF, f"{i}.pdf") for i in range(5)))
    assert server.max_inflight <= 2


@needs_real
async def test_offline_import_sample(tmp_path: Path) -> None:
    files = read_output_dir(FIXTURE_DIR)
    assert source_name_of(files) == "10_1021_acs_jmedchem_2c00949"
    kinds = {asset_kind(p) for p in files}
    assert {
        "raw_layout_json",
        "raw_middle_json",
        "raw_content_list",
        "origin_pdf",
        "image",
        "merged_image",
    } <= kinds
    assert "ir_snapshot.json" in files  # 非解析产物，按 other 登记

    db = Database(f"sqlite+aiosqlite:///{tmp_path / 'i.db'}")
    await db.create_all()
    store = LocalFsBlobStore(tmp_path / "blobs")
    async with db.session() as s:
        doc, created = await create_document(s, sha256="0" * 64, filename="x.pdf")
        assert created
        res = await store_result(s, store, doc.id, files)
    async with db.session() as s:
        assets = await list_assets(s, doc.id)
        assert len(assets) == len(files)
        rev = await get_revision(s, doc.id)
        assert rev is not None and rev.revision == res.revision == 1
        assert rev.raw_blob_refs["raw_layout_json"] == res.raw["raw_layout_json"]
        again, created = await create_document(s, sha256="0" * 64)
        assert not created and again.id == doc.id
        assert (await s.get(Document, doc.id)) is not None
    assert await store.exists(res.raw["origin_pdf"])
    await db.dispose()
