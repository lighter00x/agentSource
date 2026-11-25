"""S2.12：管线骨架（离线导入样例论文 → IR 入库 → 页面渲染），以及失败处理。"""

from __future__ import annotations

from pathlib import Path

import pytest

from drugpaper.config.settings import Settings
from drugpaper.orchestration.pipeline_graph import DocumentPipeline
from drugpaper.persistence.repos.documents import get_document
from drugpaper.persistence.repos.nodes import get_node, load_ir
from drugpaper.services import Services
from drugpaper.storage.assets import list_assets
from tests.unit.ir.conftest import FIXTURE_DIR, needs_real


@pytest.fixture
async def services(tmp_path: Path) -> Services:
    sv = Services.from_settings(
        Settings(
            database_url=f"sqlite+aiosqlite:///{tmp_path / 'p.db'}",
            blob_root=tmp_path / "blobs",
            render_dpi=36,
            mineru_url="",  # 失败用例必须隔离本机 .env 中的真实解析服务。
        )
    )
    await sv.db.create_all()
    yield sv  # type: ignore[misc]
    await sv.aclose()


@needs_real
async def test_offline_pipeline(services: Services) -> None:
    pipe = DocumentPipeline(services)
    res = await pipe.run(offline_dir=FIXTURE_DIR)
    assert res.status == "ready" and res.error is None
    assert res.node_count > 200 and res.page_images == 17 and res.revision == 1
    assert set(res.timings_ms) == {"ingest", "mineru_parse", "build_ir", "render_and_index"}
    async with services.db.session() as s:
        doc = await get_document(s, res.document_id)
        assert doc is not None and doc.status == "ready" and doc.doi == "10.1021/acs.jmedchem.2c00949"
        kinds = {a.kind for a in await list_assets(s, res.document_id)}
        assert {
            "pdf",
            "origin_pdf",
            "raw_layout_json",
            "raw_middle_json",
            "merged_image",
            "page_image",
        } <= kinds
        t1 = await get_node(s, res.document_id, "p003.g000")
        assert t1 is not None and t1.attributes["image_blob"]
        ir = await load_ir(s, res.document_id)
        assert "asset_missing" not in ir.quality.counts()
    assert await services.store.exists(t1.attributes["image_blob"])
    # 再次导入：相同内容去重，直接返回
    again = await pipe.run(offline_dir=FIXTURE_DIR)
    assert again.skipped and again.document_id == res.document_id


async def test_failure_marks_document_failed(services: Services, tmp_path: Path) -> None:
    pipe = DocumentPipeline(services, render_pages=False)
    res = await pipe.run(pdf=b"%PDF-1.4 not really", filename="x.pdf")  # 未配置 MINERU_URL
    assert res.status == "failed" and res.error is not None
    assert res.error["code"] == "mineru_parse_failed" and "MINERU_URL" in res.error["message"]
    async with services.db.session() as s:
        doc = await get_document(s, res.document_id)
        assert doc is not None and doc.status == "failed" and doc.error == res.error
    with pytest.raises(ValueError):
        await pipe.run()
