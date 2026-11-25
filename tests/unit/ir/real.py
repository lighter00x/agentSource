"""样例论文 IR（模块级缓存，多个测试文件共享）。与管线一致：使用 PDF 的真实页面尺寸与页数。"""

from __future__ import annotations

from functools import lru_cache

from drugpaper.domain.nodes import IRDocument
from drugpaper.parsers.mineru_legacy.adapter.builder import build_ir
from drugpaper.render.pages import inspect_pdf
from tests.unit.ir.conftest import LAYOUT_PATH, MIDDLE_PATH, PDF_PATH, STEM

DOC_ID = "doc_01SAMPLE0000000000000000JM"


@lru_cache(maxsize=2)
def real_ir(with_middle: bool = True) -> IRDocument:
    info = inspect_pdf(PDF_PATH.read_bytes())
    return build_ir(
        LAYOUT_PATH.read_bytes(),
        MIDDLE_PATH.read_bytes() if with_middle else None,
        document_id=DOC_ID,
        source_name=STEM,
        pdf_page_count=info.page_count,
        scanned_pages=info.scanned_pages,
        pdf_page_sizes=info.page_sizes,
    )
