from __future__ import annotations

import base64
import json
from pathlib import Path

import httpx2
import pytest

from drugpaper.gateway.moldet import MolDetClient
from drugpaper.gateway.molparser import MolParserClient, MolParserError


async def test_recognition_mixed_sources_reorder_missing_and_bad_path(tmp_path: Path):
    image = tmp_path / "a.png"
    image.write_bytes(b"local")
    seen = []

    def handler(request):
        seen.append(request)
        if request.url.path == "/health":
            return httpx2.Response(200, json={"queue_size": 0, "queue_maxsize": 10})
        if request.url.path == "/image.png":
            return httpx2.Response(200, content=b"remote")
        if b"image_2.png" in request.content:
            return httpx2.Response(
                200,
                json={
                    "results": [{"input_index": 2, "esmi": " CCO<sep> "}, {"input_index": 0, "esmi": "CC"}]
                },
            )
        return httpx2.Response(200, json={"results": [{"input_index": 0, "esmi": "CO"}]})

    client = MolParserClient(
        "http://test", http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(handler)), backoff_s=0
    )
    try:
        results = await client.recognize(
            [image, "http://test/image.png", base64.b64encode(b"base64").decode(), tmp_path / "missing.png"]
        )
        assert [r.get("esmi") for r in results] == ["CC", "CO", " CCO<sep> ", None]
        assert [r["input_index"] for r in results] == list(range(4))
        assert len([r for r in seen if r.url.path == "/recognize"]) == 2
    finally:
        await client.aclose()


async def test_batch_limit_network_retry_and_backpressure():
    posts, health = 0, 0

    def handler(request):
        nonlocal posts, health
        if request.url.path == "/health":
            health += 1
            return httpx2.Response(200, json={"queue_size": 9 if health == 1 else 0, "queue_maxsize": 10})
        posts += 1
        if posts == 1:
            return httpx2.Response(503)
        count = request.content.count(b'name="files"')
        assert count <= 32
        return httpx2.Response(
            200, json={"results": [{"input_index": i, "esmi": "CC"} for i in reversed(range(count))]}
        )

    client = MolParserClient(
        "http://test", http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(handler)), backoff_s=0
    )
    try:
        results = await client.recognize([b"image"] * 33)
        assert len(results) == 33 and posts == 3 and health == 3
    finally:
        await client.aclose()


@pytest.mark.parametrize(
    "records", [[{"input_index": 0}, {"input_index": 0}], [{"input_index": 2}], [{}], [{"input_index": True}]]
)
def test_invalid_alignment_never_binds_by_position(records):
    with pytest.raises(MolParserError):
        MolParserClient._align(records, 2)


async def test_backpressure_timeout_does_not_submit():
    client = MolParserClient(
        "http://test",
        queue_wait_s=0,
        http_client=httpx2.AsyncClient(
            transport=httpx2.MockTransport(
                lambda r: httpx2.Response(200, json={"queue_size": 10, "queue_maxsize": 10})
            )
        ),
    )
    try:
        with pytest.raises(MolParserError, match="背压"):
            await client.recognize([b"image"])
    finally:
        await client.aclose()


async def test_detector_client_reorders_and_retains_bad_slot():
    def handler(request):
        assert len(json.loads(request.content)["images"]) == 3
        return httpx2.Response(
            200, json={"results": [{"input_index": 2, "boxes": [1]}, {"input_index": 0, "boxes": [2]}]}
        )

    client = MolDetClient(
        "http://test", "token", http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(handler))
    )
    try:
        rows = await client.detect([b"a", b"bad", b"b"])
        assert [r["boxes"] for r in rows] == [[2], [], [1]] and rows[1]["error"] == "missing_result"
    finally:
        await client.aclose()


async def test_global_batch_index_anomaly_retries_each_image_without_guessing():
    calls = 0

    def handler(request):
        nonlocal calls
        if request.url.path == "/health":
            return httpx2.Response(200, json={})
        calls += 1
        count = request.content.count(b'name="files"')
        if count == 2:
            return httpx2.Response(
                200,
                json={"results": [{"input_index": 7, "esmi": "wrong"}, {"input_index": 8, "esmi": "wrong"}]},
            )
        return httpx2.Response(
            200, json={"results": [{"input_index": 9, "esmi": "CC" if b"first" in request.content else "CO"}]}
        )

    client = MolParserClient(
        "http://test", http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(handler))
    )
    try:
        rows = await client.recognize([b"first", b"second"])
        assert [r["esmi"] for r in rows] == ["CC", "CO"]
        assert [r["input_index"] for r in rows] == [0, 1]
        assert all(r["service_input_index"] == 9 for r in rows) and calls == 3
    finally:
        await client.aclose()


async def test_empty_recognition_is_reviewable_after_one_rescue():
    calls = 0

    def handler(request):
        nonlocal calls
        if request.url.path == "/health":
            return httpx2.Response(200, json={})
        calls += 1
        return httpx2.Response(200, json={"results": [{"input_index": 0, "esmi": ""}]})

    client = MolParserClient(
        "http://test", http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(handler))
    )
    try:
        assert (await client.recognize([b"image"]))[0]["error"] == "empty_esmiles"
        assert calls == 2
    finally:
        await client.aclose()
