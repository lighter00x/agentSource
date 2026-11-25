"""S2.6：渲染与裁剪（真实样例 PDF）。"""

from __future__ import annotations

from pathlib import Path

import pymupdf
import pytest

from drugpaper.render.pages import PageRenderer, inspect_pdf, png_size
from drugpaper.storage.localfs import LocalFsBlobStore
from tests.unit.ir.conftest import FIXTURE_DIR, PDF_PATH, needs_real
from tests.unit.ir.real import real_ir

pytestmark = needs_real


@pytest.fixture
async def renderer(tmp_path: Path) -> tuple[PageRenderer, str]:
    store = LocalFsBlobStore(tmp_path / "blobs")
    ref = await store.put(PDF_PATH.read_bytes(), "application/pdf")
    return PageRenderer(store, tmp_path / "cache"), ref


def test_inspect_pdf() -> None:
    info = inspect_pdf(PDF_PATH.read_bytes())
    assert info.page_count == 17 and info.scanned_pages == []
    w, h = info.page_sizes[0]
    # layout.json 的 page_size 与 PDF 页面尺寸一致（不一致时 IR 按 PDF 尺寸归一化并记 page_size_mismatch）
    assert (round(w), round(h)) == (607, 801)
    assert real_ir().pages[0].width_pt == pytest.approx(w)
    assert "page_size_mismatch" not in real_ir().quality.counts()


async def test_crop_region_contains_heading_text(renderer: tuple[PageRenderer, str]) -> None:
    """按 IR 的归一化坐标裁剪后，裁剪区域里的 PDF 文字就是该节点的文字（证明坐标换算正确）。"""
    import pymupdf as fitz

    heading = real_ir().get("p010.b005")  # EXPERIMENTAL SECTION，位于页面下部
    b = heading.geometry.regions[0].bbox
    with fitz.open(PDF_PATH) as doc:
        page = doc[10]
        rect = fitz.Rect(
            b.x0 * page.rect.width, b.y0 * page.rect.height, b.x1 * page.rect.width, b.y1 * page.rect.height
        )
        assert page.get_textbox(rect).strip().endswith("EXPERIMENTAL SECTION")


async def test_render_page_and_cache(renderer: tuple[PageRenderer, str]) -> None:
    r, pdf = renderer
    ref = await r.render_page(pdf, 0, dpi=72)
    png = await r.store.get(ref)
    assert png_size(png) == (608, 801)
    assert await r.render_page(pdf, 0, dpi=72) == ref  # 命中缓存
    assert png_size(await r.store.get(await r.render_page(pdf, 0, dpi=144))) == (1215, 1602)
    with pytest.raises(IndexError):
        await r.render_page(pdf, 99)


async def test_crop_table3_matches_merged_image(renderer: tuple[PageRenderer, str]) -> None:
    r, pdf = renderer
    t3 = real_ir().get("p007.g001")
    region = t3.geometry.regions[0]
    ref = await r.crop(pdf, region, dpi=144)
    w, h = png_size(await r.store.get(ref))
    # MinerU 的 merged_images 裁剪图：比较宽高比（两者都是同一 bbox，只是 DPI 不同）
    img = FIXTURE_DIR / t3.attributes["image_path"]
    pix = pymupdf.Pixmap(str(img))
    assert abs(w / h - pix.width / pix.height) < 0.03
    assert await r.crop(pdf, region, dpi=144) == ref
    padded = await r.crop(pdf, region, dpi=144, pad=10)
    pw, ph = png_size(await r.store.get(padded))
    assert pw > w and ph > h
