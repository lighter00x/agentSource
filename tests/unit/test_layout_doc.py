from __future__ import annotations

import pytest

from drugpaper.docsrc.layout_json import LayoutJsonSource
from drugpaper.gateway.base import ToolCall
from drugpaper.tools.builtin.layout_doc import make_layout_tools
from drugpaper.tools.protocol import ToolContext
from drugpaper.tools.registry import ToolRegistry
from tests.conftest import MINI_LAYOUT

CTX = ToolContext(run_id="r", trace_id="t")


@pytest.fixture
def source() -> LayoutJsonSource:
    return LayoutJsonSource.from_file(MINI_LAYOUT)


@pytest.fixture
def registry(source: LayoutJsonSource) -> ToolRegistry:
    return ToolRegistry(make_layout_tools(source))


def call(name: str, **args: object) -> ToolCall:
    return ToolCall(id="c", name=name, arguments=dict(args))


# ---------------------------------------------------------------- 数据源


def test_block_ids_and_skip_empty(source: LayoutJsonSource) -> None:
    ids = [b.id for b in source.blocks(0)]
    assert "p000.b007" in ids and "p000.g003" in ids
    assert "p000.b005" not in ids  # 空文本块跳过
    assert "p000.b003" not in ids  # 图像成员块没有文本
    assert source.document_id == "sample"
    assert source.pages() == [0, 1, 2, 3]


def test_group_block(source: LayoutJsonSource) -> None:
    g = source.block("p003.g000")
    assert g.kind == "group" and g.type == "image_body"
    assert g.members == ["p003.b000", "p003.b001", "p003.b002"]
    assert g.text.startswith("1-10")  # 图表块取 caption_text（F3 的错位由 P2 处理）
    assert "footnote" not in g.text and "mean of three" in g.text


def test_bbox_normalized(source: LayoutJsonSource) -> None:
    b = source.block("p000.b007")
    assert b.bbox is not None
    assert b.bbox == (round(50 / 612, 4), round(180 / 792, 4), round(300 / 612, 4), round(420 / 792, 4))
    assert all(0 <= v <= 1 for v in b.bbox)


def test_unknown_block_raises(source: LayoutJsonSource) -> None:
    with pytest.raises(KeyError):
        source.block("p009.b000")


def test_search_regex_and_literal_fallback(source: LayoutJsonSource) -> None:
    assert [h.block_id for h in source.search_text("ucm-1306")] == ["p000.b007"]
    assert source.search_text(r"D1\s*R")  # 正则
    hits = source.search_text("(26")  # 非法正则 → 字面量
    assert [h.block_id for h in hits] == ["p000.b007"]
    assert len(source.search_text("the", max_results=2)) == 2


# ---------------------------------------------------------------- 工具


async def test_grep_hits_abstract(registry: ToolRegistry) -> None:
    res = await registry.invoke(CTX, call("grep", pattern="UCM-1306"))
    assert res.ok and res.refs == ["p000.b007"]
    assert res.data and "UCM-1306" in res.data["hits"][0]["snippet"]


async def test_get_block_returns_abstract(registry: ToolRegistry) -> None:
    res = await registry.invoke(CTX, call("get_block", block_id="p000.b007"))
    assert res.ok and res.refs == ["p000.b007"]
    assert res.data and res.data["text"].startswith("ABSTRACT")
    assert res.data["page"] == 0


async def test_get_block_unknown(registry: ToolRegistry) -> None:
    res = await registry.invoke(CTX, call("get_block", block_id="p000.b999"))
    assert not res.ok and res.error and "不存在" in res.error


async def test_list_blocks_page3_contains_table1_group(registry: ToolRegistry) -> None:
    res = await registry.invoke(CTX, call("list_blocks", page=3))
    assert res.ok and res.data
    ids = [b["id"] for b in res.data["blocks"]]
    assert "p003.g000" in ids and "p003.b000" in ids
    assert set(res.refs) == set(ids)
    assert all(len(b["preview"]) <= 81 for b in res.data["blocks"])


async def test_list_blocks_bad_page(registry: ToolRegistry) -> None:
    res = await registry.invoke(CTX, call("list_blocks", page=42))
    assert not res.ok and res.error and "0~3" in res.error
