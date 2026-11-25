"""页面渲染与裁剪（执行方案 S2.6，设计文档 6.3 第 4 条、7.4 节）。

- `render_page(pdf_ref, page, dpi)`：整页 PNG；缓存键 `(pdf_sha, page, dpi)`；
- `crop(pdf_ref, region, dpi, pad)`：按归一化 bbox 裁剪；缓存键 `(pdf_sha, page, bbox, dpi, pad)`；
- 产物存入 BlobStore（按内容寻址），缓存键 → blob_ref 的映射存在 `cache_dir` 下的小文件里。

注意：PyMuPDF 采用 AGPL-3.0 / 商业双许可，对外分发或提供网络服务前需要确认许可合规。
"""

from __future__ import annotations

import asyncio
import hashlib
from dataclasses import dataclass, field
from pathlib import Path

import pymupdf

from drugpaper.domain.geometry import BBox, Region
from drugpaper.storage.base import BlobStore

PNG = "image/png"


@dataclass
class PdfInfo:
    page_count: int
    page_sizes: list[tuple[float, float]] = field(default_factory=list)
    scanned_pages: list[int] = field(default_factory=list)  # 取不到文本层的页


def inspect_pdf(pdf: bytes) -> PdfInfo:
    with pymupdf.open(stream=pdf, filetype="pdf") as doc:
        info = PdfInfo(page_count=doc.page_count)
        for i, page in enumerate(doc):
            info.page_sizes.append((float(page.rect.width), float(page.rect.height)))
            if not page.get_text("text").strip():
                info.scanned_pages.append(i)
        return info


def _render(pdf: bytes, page: int, dpi: int, clip: BBox | None, pad: float) -> bytes:
    with pymupdf.open(stream=pdf, filetype="pdf") as doc:
        if not 0 <= page < doc.page_count:
            raise IndexError(f"页码 {page} 超出范围（共 {doc.page_count} 页）")
        p = doc[page]
        rect = None
        if clip is not None:
            w, h = p.rect.width, p.rect.height
            rect = pymupdf.Rect(
                max(0.0, clip.x0 * w - pad),
                max(0.0, clip.y0 * h - pad),
                min(w, clip.x1 * w + pad),
                min(h, clip.y1 * h + pad),
            )
            if rect.is_empty:
                raise ValueError(f"裁剪区域为空：{clip}")
        pix = p.get_pixmap(dpi=dpi, clip=rect, alpha=False)
        data: bytes = pix.tobytes("png")
        return data


class PageRenderer:
    def __init__(self, store: BlobStore, cache_dir: Path | str) -> None:
        self.store = store
        self.cache_dir = Path(cache_dir)

    def _cache_file(self, key: str) -> Path:
        return self.cache_dir / hashlib.sha256(key.encode()).hexdigest()

    async def _cached(self, key: str) -> str | None:
        f = self._cache_file(key)
        if f.exists():
            ref = f.read_text(encoding="ascii").strip()
            if await self.store.exists(ref):
                return ref
        return None

    def _remember(self, key: str, ref: str) -> None:
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._cache_file(key).write_text(ref, encoding="ascii")

    async def render_page(self, pdf_ref: str, page: int, dpi: int = 144) -> str:
        key = f"page:{pdf_ref}:{page}:{dpi}"
        if (hit := await self._cached(key)) is not None:
            return hit
        pdf = await self.store.get(pdf_ref)
        png = await asyncio.to_thread(_render, pdf, page, dpi, None, 0.0)
        ref = await self.store.put(png, PNG)
        self._remember(key, ref)
        return ref

    async def crop(self, pdf_ref: str, region: Region, dpi: int = 300, pad: float = 0.0) -> str:
        """pad 为 PDF 点。"""
        b = region.bbox
        key = f"crop:{pdf_ref}:{region.page}:{b.x0:.5f},{b.y0:.5f},{b.x1:.5f},{b.y1:.5f}:{dpi}:{pad}"
        if (hit := await self._cached(key)) is not None:
            return hit
        pdf = await self.store.get(pdf_ref)
        png = await asyncio.to_thread(_render, pdf, region.page, dpi, b, pad)
        ref = await self.store.put(png, PNG)
        self._remember(key, ref)
        return ref


def png_size(data: bytes) -> tuple[int, int]:
    """从 PNG 头读取 (宽, 高)。"""
    if not data.startswith(b"\x89PNG\r\n\x1a\n"):
        raise ValueError("不是 PNG")
    return int.from_bytes(data[16:20], "big"), int.from_bytes(data[20:24], "big")
