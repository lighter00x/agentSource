from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from drugpaper.persistence.db import Database
from drugpaper.persistence.models import Document
from drugpaper.storage.assets import asset_by_path, list_assets, register_asset
from drugpaper.storage.base import BlobNotFound, sha256_hex, sniff_content_type
from drugpaper.storage.localfs import LocalFsBlobStore


async def test_put_get_exists_url(tmp_path: Path) -> None:
    store = LocalFsBlobStore(tmp_path)
    ref = await store.put(b"hello")
    assert ref == sha256_hex(b"hello")
    assert await store.get(ref) == b"hello"
    assert await store.exists(ref)
    assert store.path_for(ref) == tmp_path / "default" / ref[:2] / ref[2:4] / ref
    assert await store.url(ref) == f"/api/v1/blobs/{ref}"
    assert str(tmp_path) not in await store.url(ref)


async def test_concurrent_same_content_single_copy(tmp_path: Path) -> None:
    store = LocalFsBlobStore(tmp_path)
    data = b"x" * 100_000
    refs = await asyncio.gather(*(store.put(data) for _ in range(20)))
    assert len(set(refs)) == 1
    files = await asyncio.to_thread(lambda: [p for p in tmp_path.rglob("*") if p.is_file()])
    assert len(files) == 1 and files[0].read_bytes() == data  # noqa: ASYNC240


async def test_missing_and_invalid_refs(tmp_path: Path) -> None:
    store = LocalFsBlobStore(tmp_path)
    with pytest.raises(BlobNotFound):
        await store.get("0" * 64)
    with pytest.raises(BlobNotFound):
        await store.get("../../etc/passwd")
    assert not await store.exists("not-a-ref")
    with pytest.raises(ValueError):
        LocalFsBlobStore(tmp_path, user_id="../x")


def test_sniff() -> None:
    assert sniff_content_type(b"%PDF-1.7") == "application/pdf"
    assert sniff_content_type(b"\x89PNG\r\n\x1a\nxx") == "image/png"
    assert sniff_content_type(b"\xff\xd8\xff\xe0") == "image/jpeg"
    assert sniff_content_type(b'  {"a":1}') == "application/json"


async def test_register_asset_idempotent(tmp_path: Path) -> None:
    db = Database(f"sqlite+aiosqlite:///{tmp_path / 'a.db'}")
    await db.create_all()
    async with db.session() as s:
        s.add(Document(id="doc_1", sha256="0" * 64))
    async with db.session() as s:
        a1 = await register_asset(s, "doc_1", "image", "a" * 64, path="images/x.jpg")
        a2 = await register_asset(s, "doc_1", "image", "a" * 64, path="images/x.jpg")
        assert a1.id == a2.id and a1.id.startswith("ast_")
        await register_asset(s, "doc_1", "origin_pdf", "b" * 64)
    async with db.session() as s:
        assert len(await list_assets(s, "doc_1")) == 2
        assert (await asset_by_path(s, "doc_1", "images/x.jpg")) is not None
    await db.dispose()
