from __future__ import annotations

import json

import httpx2
import pytest

from drugpaper.gateway.molparser import MolParserClient, MolParserError, extract_esmiles


async def test_molparser_http_protocol_and_rec_only_override() -> None:
    seen: list[httpx2.Request] = []
    esmi = " C*<sep><a>1:R[1]</a> "

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen.append(request)
        if request.url.path == "/health":
            return httpx2.Response(200, json={"status": "ok"})
        return httpx2.Response(200, json={"results": [{"esmi": esmi, "bbox": None}]})

    client = MolParserClient(
        "http://molparser.test/",
        rec_only=False,
        http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(handler)),
    )
    try:
        assert await client.health() == {"status": "ok"}
        results = await client.recognize_files(["mol.png"], blobs=[b"image-bytes"])
        assert extract_esmiles(results) == [esmi]
        assert b'name="files"' in seen[-1].content
        assert b"image-bytes" in seen[-1].content
        assert b'name="rec_only"\r\n\r\nfalse' in seen[-1].content
        await client.recognize_json([{"url": "http://images.test/mol.png"}], rec_only=True)
        assert seen[-1].url.path == "/recognize/json"
        assert json.loads(seen[-1].content) == {
            "images": [{"url": "http://images.test/mol.png"}],
            "rec_only": True,
        }
    finally:
        await client.aclose()


@pytest.mark.parametrize("payload", [{}, {"results": None}, {"results": ["bad"]}])
async def test_molparser_rejects_invalid_service_results(payload: dict[str, object]) -> None:
    client = MolParserClient(
        "http://molparser.test",
        http_client=httpx2.AsyncClient(
            transport=httpx2.MockTransport(lambda request: httpx2.Response(200, json=payload))
        ),
    )
    try:
        with pytest.raises(MolParserError, match="results"):
            await client.recognize_json(["encoded"])
    finally:
        await client.aclose()


def test_esmiles_preserves_raw_caption_fallback() -> None:
    raw = " C*<sep><a>1:R[1]</a> "
    assert extract_esmiles([{"esmi": "", "caption": "", "raw_caption": raw}]) == [raw]
    with pytest.raises(ValueError):
        extract_esmiles([{"esmi": " "}])
