"""S2.13：API 全链路——上传（模拟 MinerU 服务返回样例论文产物）→ 管线 → 文档树、节点、页面图、裁剪、检索、blob。"""

from __future__ import annotations

import base64
from collections.abc import Iterator
from pathlib import Path
from urllib.parse import quote

import httpx2
import pytest
from fastapi.testclient import TestClient

from drugpaper.api.app import create_app, export_openapi
from drugpaper.config.settings import Settings
from drugpaper.parsers.mineru_legacy.client import MinerUClient
from drugpaper.parsers.mineru_legacy.importer import read_output_dir
from drugpaper.render.pages import png_size
from drugpaper.services import Services
from tests.unit.ir.conftest import FIXTURE_DIR, PDF_PATH, needs_real

pytestmark = needs_real
API = "/api/v1"


def _fake_mineru() -> MinerUClient:
    files = read_output_dir(FIXTURE_DIR)
    payload = {
        "files": [{"path": p, "content_base64": base64.b64encode(d).decode()} for p, d in files.items()],
        "status": "succeeded",
    }

    def handler(req: httpx2.Request) -> httpx2.Response:
        if req.method == "POST":
            return httpx2.Response(202, json={"task_id": "t1"})
        if req.url.path.endswith("/result"):
            return httpx2.Response(200, json=payload)
        return httpx2.Response(200, json={"status": "succeeded", "result": {"doc_out": "/x"}})

    return MinerUClient(
        "http://mineru.test",
        poll_interval_s=0,
        http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(handler)),
    )


@pytest.fixture(scope="module")
def client(tmp_path_factory: pytest.TempPathFactory) -> Iterator[TestClient]:
    tmp: Path = tmp_path_factory.mktemp("api")
    sv = Services.from_settings(
        Settings(database_url=f"sqlite+aiosqlite:///{tmp / 'api.db'}", blob_root=tmp / "blobs", render_dpi=36)
    )
    sv._mineru = _fake_mineru()
    with TestClient(create_app(sv)) as c:
        yield c
        assert c.portal is not None
        c.portal.call(sv.aclose)  # 在应用所在的事件循环里关闭连接池


@pytest.fixture(scope="module")
def doc_id(client: TestClient) -> str:
    r = client.post(
        f"{API}/documents", files={"file": ("paper.pdf", PDF_PATH.read_bytes(), "application/pdf")}
    )
    assert r.status_code == 202, r.text
    return str(r.json()["document_id"])


def test_upload_runs_pipeline(client: TestClient, doc_id: str) -> None:
    info = client.get(f"{API}/documents/{doc_id}").json()
    assert info["status"] == "ready", info["error"]
    assert info["doi"] == "10.1021/acs.jmedchem.2c00949" and info["active_revision"] == 1
    assert info["quality"]["issue_counts"]["caption_mismatch"] >= 2
    again = client.post(
        f"{API}/documents", files={"file": ("x.pdf", PDF_PATH.read_bytes(), "application/pdf")}
    )
    assert again.json() == {"document_id": doc_id, "status": "ready", "duplicate": True}
    assert any(d["document_id"] == doc_id for d in client.get(f"{API}/documents").json()["documents"])


def test_upload_rejects_non_pdf(client: TestClient) -> None:
    r = client.post(f"{API}/documents", files={"file": ("x.pdf", b"hello", "application/pdf")})
    assert r.status_code == 400 and r.json()["error"]["code"] == "NOT_PDF"


def test_tree(client: TestClient, doc_id: str) -> None:
    tree = client.get(f"{API}/documents/{doc_id}/tree").json()
    labels = [c.get("label") for c in tree["root"]["children"]]
    assert "RESULTS AND DISCUSSION" in labels and tree["revision"] == 1


def test_node_detail_and_crop(client: TestClient, doc_id: str) -> None:
    ref = f"{doc_id}/r1/p007.g001"
    n = client.get(f"{API}/nodes/{ref}").json()
    assert n["type"] == "table" and n["label"] == "Table 3" and n["regions"][0]["page"] == 7
    assert n["children"][0]["type"] in ("figure_part", "caption")
    assert n["attributes"]["image_blob"]
    crop = client.get(f"{API}/nodes/{ref}/crop", params={"dpi": 72})
    assert crop.status_code == 200 and crop.headers["content-type"] == "image/png"
    w, h = png_size(crop.content)
    assert 200 < w < 260 and 190 < h < 230  # 约 242×210 pt @72dpi
    blob = client.get(f"{API}/blobs/{n['attributes']['image_blob']}")
    assert blob.status_code == 200 and blob.headers["content-type"] == "image/jpeg"
    assert client.get(f"{API}/nodes/{ref}/crop", params={"region": 5}).status_code == 404
    assert client.get(f"{API}/nodes/{doc_id}/r1/p099.b000").json()["error"]["code"] == "NOT_FOUND"
    assert client.get(f"{API}/nodes/not-a-ref").json()["error"]["code"] == "BAD_NODE_REF"
    encoded = client.get(f"{API}/nodes/{quote(ref, safe='')}")  # URL 编码的引用也可以
    assert encoded.status_code == 200


def test_page_image_and_nodes(client: TestClient, doc_id: str) -> None:
    img = client.get(f"{API}/documents/{doc_id}/pages/0/image", params={"dpi": 72})
    assert img.status_code == 200 and png_size(img.content) == (608, 801)
    assert client.get(f"{API}/documents/{doc_id}/pages/99/image").status_code == 404
    nodes = client.get(f"{API}/documents/{doc_id}/pages/0/nodes").json()["nodes"]
    ids = {x["local_id"] for x in nodes}
    assert {"p000.b007", "p000.g007", "p000.b011"} <= ids
    assert all(x["regions"] for x in nodes)
    with_discarded = client.get(
        f"{API}/documents/{doc_id}/pages/0/nodes", params={"include_discarded": True}
    ).json()
    assert len(with_discarded["nodes"]) == len(nodes) + 13


def test_search_modes(client: TestClient, doc_id: str) -> None:
    g = client.get(
        f"{API}/documents/{doc_id}/search", params={"q": r"\b26\b", "mode": "grep", "limit": 5}
    ).json()
    assert len(g["results"]) == 5 and g["truncated"] and g["results"][0]["char_span"]
    f = client.get(f"{API}/documents/{doc_id}/search", params={"q": "hERG", "mode": "fts"}).json()
    assert f["results"] and f["results"][0]["score"] > 0
    h = client.get(f"{API}/documents/{doc_id}/search", params={"q": "dopamine D1 receptor"}).json()
    assert h["mode"] == "hybrid" and h["results"]
    bad = client.get(f"{API}/documents/{doc_id}/search", params={"q": "(26", "mode": "grep"})
    assert bad.status_code == 400 and bad.json()["error"]["code"] == "BAD_PATTERN"
    missing = client.get(f"{API}/documents/doc_nope/search", params={"q": "x"})
    assert missing.status_code == 404 and missing.json()["error"]["code"] == "NOT_FOUND"
    invalid = client.get(f"{API}/documents/{doc_id}/search", params={"q": "x", "mode": "nope"})
    assert invalid.status_code == 422 and invalid.json()["error"]["code"] == "VALIDATION_ERROR"


def test_openapi_export(tmp_path: Path) -> None:
    spec = export_openapi(tmp_path / "openapi.json")
    paths = spec["paths"]
    for p in [
        "/api/v1/documents",
        "/api/v1/documents/{document_id}",
        "/api/v1/documents/{document_id}/tree",
        "/api/v1/documents/{document_id}/pages/{page}/image",
        "/api/v1/documents/{document_id}/pages/{page}/nodes",
        "/api/v1/nodes/{node_ref}",
        "/api/v1/nodes/{node_ref}/crop",
        "/api/v1/documents/{document_id}/search",
        "/api/v1/blobs/{ref}",
    ]:
        assert p in paths, p
    assert "ErrorResponse" in spec["components"]["schemas"]


def test_existing_revision_exposes_typed_ocr_without_reimport(client: TestClient, doc_id: str) -> None:
    n = client.get(f"{API}/nodes/{doc_id}/r1/p001.b001").json()
    assert n["text_raw"]
    assert "lines" not in n["attributes"]
    spans = [span for line in n["attributes"]["ocr_lines"] for span in line["spans"]]
    assert any(span["type"] == "inline_equation" for span in spans)
    assert all(set(span) == {"type", "content"} for span in spans)
