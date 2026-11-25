"""本地文件系统 BlobStore：`root/{user_id}/{ref[:2]}/{ref[2:4]}/{ref}`，原子写入、天然去重。"""

from __future__ import annotations

import asyncio
import os
import tempfile
from pathlib import Path

from drugpaper.storage.base import BlobNotFound, is_valid_ref, sha256_hex

API_PREFIX = "/api/v1/blobs"


class LocalFsBlobStore:
    def __init__(self, root: Path | str, user_id: str = "default") -> None:
        if not user_id or "/" in user_id or "\\" in user_id or user_id in (".", ".."):
            raise ValueError(f"非法 user_id：{user_id!r}")
        self.root = Path(root)
        self.user_id = user_id

    def path_for(self, ref: str) -> Path:
        if not is_valid_ref(ref):
            raise BlobNotFound(f"非法 blob_ref：{ref!r}")
        return self.root / self.user_id / ref[:2] / ref[2:4] / ref

    def _write(self, ref: str, data: bytes) -> None:
        target = self.path_for(ref)
        if target.exists():
            return
        target.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=target.parent, prefix=".tmp-")
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(data)
            try:
                os.replace(tmp, target)
            except PermissionError:
                # Windows：另一个写入者已经把相同内容放好，目标被占用
                if not target.exists():
                    raise
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)

    async def put(self, data: bytes, content_type: str = "application/octet-stream") -> str:
        ref = sha256_hex(data)
        await asyncio.to_thread(self._write, ref, data)
        return ref

    async def get(self, ref: str) -> bytes:
        p = self.path_for(ref)
        try:
            return await asyncio.to_thread(p.read_bytes)
        except FileNotFoundError:
            raise BlobNotFound(ref) from None

    async def exists(self, ref: str) -> bool:
        try:
            return self.path_for(ref).exists()
        except BlobNotFound:
            return False

    async def url(self, ref: str, ttl_s: int = 600) -> str:
        return f"{API_PREFIX}/{ref}"
