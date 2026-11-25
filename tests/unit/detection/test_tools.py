from __future__ import annotations

import json

import pymupdf
import pytest

from drugpaper.config.settings import Settings
from drugpaper.detection.models import RawDetection
from drugpaper.detection.service import MoleculeDetectionService
from drugpaper.domain.geometry import BBox, Geometry, Region
from drugpaper.domain.nodes import DocumentNode, IRDocument
from drugpaper.gateway.base import ToolCall
from drugpaper.persistence.models import Document
from drugpaper.persistence.repos.nodes import save_ir
from drugpaper.services import Services
from drugpaper.tools.builtin.molecule_detection import make_detection_tools
from drugpaper.tools.protocol import ToolContext
from drugpaper.tools.registry import ToolRegistry
from tests.unit.detection.test_service import FakeBackend


@pytest.fixture
async def env(tmp_path):
    sv = Services.from_settings(
        Settings(
            _env_file=None,
            database_url=f"sqlite+aiosqlite:///{tmp_path}/db",
            blob_root=tmp_path / "blobs",
            crop_dpi=72,
        )
    )
    await sv.db.create_all()
    document = pymupdf.open()
    page = document.new_page(width=100, height=100)
    page.insert_text((15, 40), "CH3")
    pdf = document.tobytes()
    document.close()
    for user in ("a", "b"):
        sha = await sv.store_for(user).put(pdf)
        async with sv.db.session(user) as session:
            session.add(Document(id=f"doc_{user}", user_id=user, sha256=sha))
            await session.flush()
            await save_ir(
                session,
                IRDocument(
                    document_id=f"doc_{user}",
                    nodes=[
                        DocumentNode(
                            document_id=f"doc_{user}",
                            revision=1,
                            local_id="p000.g000",
                            node_type="figure",
                            geometry=Geometry(regions=[Region(page=0, bbox=BBox(x0=0, y0=0, x1=1, y1=1))]),
                        )
                    ],
                ),
                user_id=user,
            )
    backend = FakeBackend()
    sv._detector = MoleculeDetectionService(backend, sv.settings)
    yield sv, ToolRegistry(make_detection_tools(sv)), backend
    await sv.aclose()


async def call(tools, user, name, **args):
    return await tools.invoke(
        ToolContext(run_id="r", trace_id="t", user_id=user), ToolCall(id="c", name=name, arguments=args)
    )


async def test_detect_page_crop_annotation_and_cache(env):
    sv, tools, backend = env
    result = await call(tools, "a", "molecule.detect", target_ref="doc_a/r1/p000.g000")
    assert result.ok and result.data["count"] == 1
    ref = result.data["result_ref"]
    listed = await call(tools, "a", "molecule.detections", result_ref=ref)
    assert listed.ok and not listed.truncated
    item = listed.data["detections"][0]
    crops = await call(tools, "a", "molecule.crop", result_ref=ref, label_ids=[1])
    assert crops.ok and crops.data["crops"][0]["detection_id"] == item["detection_id"]
    assert await sv.store_for("a").exists(crops.data["crops"][0]["image_ref"])
    annotation = await call(tools, "a", "som.render", result_ref=ref)
    assert annotation.ok and await sv.store_for("a").exists(annotation.data["image_ref"])
    hit = await call(tools, "a", "molecule.detect", target_ref="doc_a/r1/p000.g000")
    assert hit.ok and hit.data["cached"] and hit.data["result_ref"] == ref
    assert backend.calls == 1
    bad = await call(tools, "a", "molecule.crop", result_ref=ref, label_ids=[99])
    assert not bad.ok and bad.error == "E_INVALID_LABEL"


async def test_user_cannot_detect_or_read_other_users_assets(env):
    sv, tools, backend = env
    result = await call(tools, "a", "molecule.detect", target_ref="doc_a/r1/p000.g000")
    denied = await call(tools, "b", "molecule.detect", target_ref="doc_a/r1/p000.g000")
    assert not denied.ok and denied.error == "E_FORBIDDEN"
    for name, args in [
        ("molecule.detections", {}),
        ("som.render", {}),
        ("molecule.crop", {"label_ids": [1]}),
    ]:
        denied = await call(tools, "b", name, result_ref=result.data["result_ref"], **args)
        assert not denied.ok and denied.error == "E_RESULT_NOT_FOUND"
    second = await call(tools, "b", "molecule.detect", target_ref="doc_b/r1/p000.g000")
    assert second.ok and second.data["result_ref"] != result.data["result_ref"]
    # 即使把结果文件拷到另一 namespace，文件内 owner 和文档权限仍需校验。
    copied = await sv.store_for("b").put(await sv.store_for("a").get(result.data["result_ref"]))
    denied = await call(tools, "b", "molecule.detections", result_ref=copied)
    assert not denied.ok and denied.error == "E_FORBIDDEN"
    assert backend.calls == 2


async def test_pagination_and_invalid_region(env):
    sv, tools, backend = env
    backend.boxes = [
        RawDetection(bbox=(10, 20, 40, 60), confidence=0.9, class_id=0, class_name="molecule")
        for _ in range(25)
    ]
    result = await call(tools, "a", "molecule.detect", target_ref="doc_a/r1/p000.g000")
    ref = result.data["result_ref"]
    labels = []
    offset = 0
    while offset is not None:
        page = await call(tools, "a", "molecule.detections", result_ref=ref, offset=offset)
        assert page.ok and not page.truncated
        labels.extend(i["label_id"] for i in page.data["detections"])
        offset = page.data["next_offset"]
    assert labels == list(range(1, 26))
    invalid = await call(
        tools, "a", "molecule.detect", target_ref="doc_a/r1/p000.g000", parent_region_index=9
    )
    assert not invalid.ok and invalid.error == "E_INVALID_TARGET"
    # 不向其他文档绑定的会话返回结果。
    scoped = ToolContext(run_id="r", trace_id="t", user_id="a", document_id="doc_b")
    denied = await tools.invoke(
        scoped, ToolCall(id="c", name="molecule.detections", arguments={"result_ref": ref})
    )
    assert not denied.ok and denied.error == "E_FORBIDDEN"
    saved = json.loads(await sv.store_for("a").get(ref))
    assert saved["parent_region_index"] == 0
