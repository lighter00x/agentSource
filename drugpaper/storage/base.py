"""BlobStore 协议（设计文档 16.3 节）。"""

from __future__ import annotations

import hashlib
import re
from typing import Protocol

REF_RE = re.compile(r"^[0-9a-f]{64}$")


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def is_valid_ref(ref: str) -> bool:
    return bool(REF_RE.match(ref))


class BlobNotFound(KeyError):
    pass


class BlobStore(Protocol):
    async def put(self, data: bytes, content_type: str = "application/octet-stream") -> str:
        """返回 blob_ref（sha256 十六进制）。相同内容只存一份。"""
        ...

    async def get(self, ref: str) -> bytes: ...

    async def exists(self, ref: str) -> bool: ...

    async def url(self, ref: str, ttl_s: int = 600) -> str:
        """本地实现返回 API 代理地址，不暴露文件路径。"""
        ...


def sniff_content_type(data: bytes) -> str:
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith(b"%PDF-"):
        return "application/pdf"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    head = data[:64].lstrip()
    if head.startswith((b"{", b"[")):
        return "application/json"
    return "application/octet-stream"
