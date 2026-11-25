"""图片尺寸工具：按最长边缩放（任务的 `image_max_side`）。只依赖 PyMuPDF。"""

from __future__ import annotations

import pymupdf


def image_size(data: bytes) -> tuple[int, int]:
    pix = pymupdf.Pixmap(data)
    return pix.width, pix.height


def limit_max_side(data: bytes, max_side: int) -> tuple[bytes, str]:
    """最长边超过 max_side 时等比缩小并转为 PNG；否则原样返回。返回 (数据, MIME)。"""
    pix = pymupdf.Pixmap(data)
    longest = max(pix.width, pix.height)
    if longest <= max_side:
        from drugpaper.storage.base import sniff_content_type

        return data, sniff_content_type(data)
    scale = max_side / longest
    with pymupdf.open(stream=data) as doc:  # 图片作为单页文档打开
        page = doc[0]
        zoom = scale * pix.width / page.rect.width
        out = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), alpha=False)
        png: bytes = out.tobytes("png")
    return png, "image/png"
