"""基于真实样例论文 10_1021_acs_jmedchem_2c00949 的测试（执行方案 S1.5 验证项 + 端到端离线问答）。

fixtures 来自 legacy_mineru_merge/test/10_1021_acs_jmedchem_2c00949/auto，缺失时整体跳过。
"""

from __future__ import annotations

import re

import pytest

from drugpaper.agent.config import AgentConfig
from drugpaper.agent.defaults import StaticToolset
from drugpaper.agent.qa import load_prompt
from drugpaper.agent.runtime import AgentRuntime
from drugpaper.agent.state import AgentAnswer, Citation, initial_state
from drugpaper.agent.validators import CitationValidator
from drugpaper.docsrc.layout_json import LayoutJsonSource
from drugpaper.gateway.base import ToolCall
from drugpaper.tools.builtin.layout_doc import LAYOUT_TOOL_NAMES, make_layout_tools
from drugpaper.tools.protocol import ToolContext
from drugpaper.tools.registry import ToolRegistry
from tests.conftest import real_layout_path
from tests.fakes.fake_llm import FakeLLM, ListTracer, call, submit, tools

REAL = real_layout_path()
pytestmark = pytest.mark.skipif(
    REAL is None, reason="样例论文 fixtures 未放入 tests/fixtures/jmedchem_2c00949/"
)

ABSTRACT = "p000.b007"
CTX = ToolContext(run_id="real", trace_id="t")
ID_RE = re.compile(r"^p\d{3}\.[bg]\d{3}$")


@pytest.fixture(scope="module")
def source() -> LayoutJsonSource:
    assert REAL is not None
    return LayoutJsonSource.from_file(REAL)


@pytest.fixture(scope="module")
def registry(source: LayoutJsonSource) -> ToolRegistry:
    return ToolRegistry(make_layout_tools(source))


def tc(name: str, **args: object) -> ToolCall:
    return ToolCall(id="c", name=name, arguments=dict(args))


# ---------------------------------------------------------------- 数据源结构


def test_document_shape(source: LayoutJsonSource) -> None:
    assert source.document_id == "10_1021_acs_jmedchem_2c00949"
    assert source.pages() == list(range(17))
    blocks = source.blocks()
    assert len(blocks) > 100
    assert all(ID_RE.match(b.id) for b in blocks)
    assert len({b.id for b in blocks}) == len(blocks)
    assert all(b.text for b in blocks)  # 空文本块已跳过
    assert all(b.bbox is not None and all(0 <= v <= 1 for v in b.bbox) for b in blocks)
    assert all(b.bbox[0] <= b.bbox[2] and b.bbox[1] <= b.bbox[3] for b in blocks if b.bbox)


def test_merge_groups(source: LayoutJsonSource) -> None:
    groups = [b for b in source.blocks() if b.kind == "group"]
    assert {"p003.g000", "p005.g000", "p007.g001"} <= {g.id for g in groups}
    assert all(g.members for g in groups)
    t1 = source.block("p003.g000")
    assert t1.members == ["p003.b000", "p003.b001", "p003.b002"]
    # F3：Table 1 合并组的 caption 是母核标签 "1-10"，真正的标题在成员 title 块中（P2 修正）
    assert t1.text.startswith("1-10")
    assert source.block("p003.b000").text.startswith("Table 1.")


# ---------------------------------------------------------------- S1.5 验证项


async def test_grep_ucm1306_hits_abstract(registry: ToolRegistry) -> None:
    res = await registry.invoke(CTX, tc("grep", pattern="UCM-1306"))
    assert res.ok and ABSTRACT in res.refs
    assert res.refs[0] == "p000.b000"  # 标题块
    assert all("UCM-1306" in h["snippet"] for h in res.data["hits"])  # type: ignore[index]


async def test_get_block_returns_abstract(registry: ToolRegistry) -> None:
    res = await registry.invoke(CTX, tc("get_block", block_id=ABSTRACT))
    assert res.ok and res.data
    text = res.data["text"]
    assert text.startswith("ABSTRACT:")
    assert "compound 26 [UCM-1306" in text
    assert res.data["page"] == 0 and res.data["type"] == "text"


async def test_list_blocks_page3_contains_table1_group(registry: ToolRegistry) -> None:
    res = await registry.invoke(CTX, tc("list_blocks", page=3))
    assert res.ok and "p003.g000" in res.refs


# ---------------------------------------------------------------- F7：LaTeX 文本的匹配


def test_grep_latex_fallback(source: LayoutJsonSource) -> None:
    raw_only = [b.id for b in source.blocks() if re.search("D1 ?receptor", b.text, re.I)]
    hits = [h.block_id for h in source.search_text(r"D1 ?receptor", 50)]
    assert ABSTRACT not in raw_only and ABSTRACT in hits  # 原文是 \mathrm { D } _ { 1 }receptor
    assert len(hits) > len(raw_only)
    snippet = next(h.snippet for h in source.search_text(r"D1 ?receptor", 50) if h.block_id == ABSTRACT)
    assert "D1 receptor" in snippet and "\\mathrm" not in snippet


@pytest.mark.parametrize(
    "quote",
    [
        "compound 26 [UCM-1306",
        "In search for selective modulators of the D1 receptor",  # LaTeX + 缺空格
        "Tolerance development caused by dopamine replacement",  # 行尾连字符 re-placement
        "increases the dopamine maximal effect",  # dop-amine
    ],
)
def test_quote_validation_tolerates_mineru_artifacts(source: LayoutJsonSource, quote: str) -> None:
    state = initial_state("r", "q", {})
    state["seen_refs"] = [ABSTRACT]
    ans = AgentAnswer(answer="x", citations=[Citation(ref=ABSTRACT, quote=quote)])
    assert CitationValidator(source).validate(ans, state) == []


def test_quote_validation_still_rejects_wrong_text(source: LayoutJsonSource) -> None:
    state = initial_state("r", "q", {})
    state["seen_refs"] = [ABSTRACT]
    ans = AgentAnswer(answer="x", citations=[Citation(ref=ABSTRACT, quote="compound 27 [UCM-1306")])
    assert len(CitationValidator(source).validate(ans, state)) == 1


# ---------------------------------------------------------------- 端到端（FakeLLM + 真实论文）


async def test_end_to_end_qa_on_real_paper(source: LayoutJsonSource, registry: ToolRegistry) -> None:
    config = AgentConfig(
        name="doc_qa",
        system_prompt=load_prompt("qa_system.md"),
        toolset=StaticToolset(registry, LAYOUT_TOOL_NAMES),
        validators=[CitationValidator(source)],
    )
    llm = FakeLLM(
        [
            tools(call("grep", {"pattern": "UCM-1306"})),
            tools(call("get_block", {"block_id": ABSTRACT})),
            submit(
                "UCM-1306 是论文中的化合物 26，一种多巴胺 D1 受体的正向变构调节剂。",
                [{"ref": ABSTRACT, "quote": "compound 26 [UCM-1306"}],
            ),
        ]
    )
    tracer = ListTracer()
    res = await AgentRuntime(config, llm, registry, tracer).run(
        "UCM-1306 对应论文中的哪个化合物编号？",
        ToolContext(run_id="e2e", trace_id="t", document_id=source.document_id),
    )
    assert res.stop_reason == "answered", res.warnings
    assert res.steps == 3 and res.answer["warnings"] == []
    # 模型在第 3 步看到的 get_block 结果就是摘要原文
    last_tool = [m for m in llm.requests[2].messages if m.role == "tool"][-1]
    assert "compound 26 [UCM-1306" in last_tool.text()
    assert [p["name"] for p in tracer.of("tool_call")] == ["grep", "get_block"]
