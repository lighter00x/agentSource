"""S2.10：IrDocumentSource、doc_read / search 工具包，以及 AgentRuntime 在 IR 上问答（FakeLLM）。"""

from __future__ import annotations

import json
from typing import Any

import pytest
from langgraph.checkpoint.memory import InMemorySaver

from drugpaper.agent.qa import build_ir_qa_agent, resolve_evidence
from drugpaper.docsrc.ir_source import IrDocumentSource
from drugpaper.gateway.base import ChatRequest, ChatResponse, ToolCall
from drugpaper.tools.builtin.doc_read import make_doc_read_tools
from drugpaper.tools.builtin.search import make_search_tools
from drugpaper.tools.protocol import ToolContext
from drugpaper.tools.registry import ToolRegistry
from tests.fakes.fake_llm import FakeLLM, ListTracer, call, submit, tools
from tests.unit.ir.conftest import needs_real
from tests.unit.ir.real import DOC_ID, real_ir

pytestmark = needs_real
CTX = ToolContext(run_id="r", trace_id="t", document_id=DOC_ID)
REF = f"{DOC_ID}/r1/"


@pytest.fixture(scope="module")
def src() -> IrDocumentSource:
    return IrDocumentSource(real_ir())


@pytest.fixture(scope="module")
def reg(src: IrDocumentSource) -> ToolRegistry:
    return ToolRegistry([*make_doc_read_tools(src), *make_search_tools(src)])


async def inv(reg: ToolRegistry, name: str, **args: Any) -> Any:
    return await reg.invoke(CTX, ToolCall(id="c", name=name, arguments=args))


# ---------------------------------------------------------------- 数据源


def test_document_source_protocol(src: IrDocumentSource) -> None:
    assert src.document_id == DOC_ID and src.pages() == list(range(17))
    b = src.block(REF + "p000.b007")
    assert b.id == REF + "p000.b007" and b.text.startswith("ABSTRACT") and b.page == 0
    assert src.block("p000.b007").id == b.id  # 局部 ID 也可以
    t3 = src.block("p007.g001")
    assert t3.kind == "group" and t3.text.startswith("Table 3. ADMET")  # 视觉节点取图注
    assert src.search_text("UCM-1306")[0].block_id.startswith(REF)
    assert all(v.type not in ("section", "discarded") for v in src.blocks(0))
    with pytest.raises(KeyError):
        src.node("doc_OTHER/r1/p000.b007")


def test_outline_and_sections(src: IrDocumentSource) -> None:
    o = src.outline()
    assert o["doi"] == "10.1021/acs.jmedchem.2c00949"
    labels = {v["label"] for v in o["figures_tables"]}
    assert {"Table 1", "Table 3", "Scheme 5", "Figure 8"} <= labels
    assert src.section("experimental").label == "EXPERIMENTAL SECTION"
    assert src.section("RESULTS AND DISCUSSION").section_role == "results"
    assert [n.local_id for n in src.window("p000.b011")] == ["p000.b010", "p000.b011", "p000.b013"]


# ---------------------------------------------------------------- 工具


async def test_doc_read_tools(reg: ToolRegistry) -> None:
    o = await inv(reg, "get_outline")
    assert o.ok and REF + "p007.g001" in o.refs
    n = await inv(reg, "get_node", node_id=REF + "p007.g001")
    assert n.ok and n.data["label"] == "Table 3" and n.data["regions"][0]["page"] == 7
    assert "note" in n.data
    kids = await inv(reg, "get_children", node_id="p003.g000")
    assert kids.ok and kids.refs[0] == REF + "p003.g000.c0"
    win = await inv(reg, "get_context_window", node_id="p000.b011", before=1, after=0)
    assert [x["id"] for x in win.data["nodes"]] == [REF + "p000.b010", REF + "p000.b011"]
    sec = await inv(reg, "get_section", section="conclusion")
    assert sec.ok and sec.data["label"] == "CONCLUSIONS" and REF + "p010.b003" in sec.refs
    page = await inv(reg, "get_page_text", page=7, max_chars=1500)
    assert page.ok and page.data.get("note")
    bad = await inv(reg, "get_node", node_id="p099.b000")
    assert not bad.ok and "不存在" in bad.error
    assert not (await inv(reg, "get_section", section="nope")).ok
    assert not (await inv(reg, "get_page_text", page=40)).ok


async def test_grep_and_search_tools(reg: ToolRegistry) -> None:
    g = await inv(reg, "grep", pattern=r"\b26\b", sections=["abstract"])
    assert g.ok and g.refs == [REF + "p000.b007"]
    assert g.data["matches"][0]["char_span"][1] > g.data["matches"][0]["char_span"][0]
    bad = await inv(reg, "grep", pattern="(26")
    assert not bad.ok and "literal" in bad.error
    s = await inv(reg, "search", query="hERG inhibition", mode="fts")
    assert s.ok and s.refs
    t = await inv(reg, "search", query="Table 3 ADMET")
    assert t.ok and REF + "p007.g001.c0" in t.refs


# ---------------------------------------------------------------- Agent 在 IR 上问答


def _script() -> list[Any]:
    return [
        tools(call("get_outline")),
        tools(call("grep", {"pattern": "Table 3", "node_types": ["caption"]})),
        submit(
            "Table 3 的标题是 “ADMET Profile for Compound 26”。",
            [{"ref": REF + "p007.g001.c0", "quote": "ADMET Profile for Compound 26"}],
        ),
    ]


async def test_agent_answers_on_ir_with_regions(src: IrDocumentSource) -> None:
    llm = FakeLLM(_script())
    agent = build_ir_qa_agent(src, llm, ListTracer())
    res = await agent.ask("文中 Table 3 的标题是什么？")
    assert res.stop_reason == "answered", res.warnings
    assert {t.name for t in llm.requests[0].tools} == {
        "get_outline",
        "grep",
        "get_node",
        "find_capabilities",
        "describe_capability",
        "load_capability",
        "unload_capability",
        "read_skill_resource",
        "submit_answer",
    }
    ev = res.evidence[0]
    assert ev["node_id"] == REF + "p007.g001.c0"
    assert ev["regions"][0]["page"] == 7 and 0 <= ev["regions"][0]["bbox"]["x0"] < 1
    raw = src.node("p007.g001.c0").text_raw
    assert raw[ev["char_span"][0] : ev["char_span"][1]] == "ADMET Profile for Compound 26"


async def test_first_mention_of_compound_26(src: IrDocumentSource) -> None:
    def step(req: ChatRequest) -> ChatResponse:
        tool_msgs = [m for m in req.messages if m.role == "tool"]
        if not tool_msgs:
            return tools(call("grep", {"pattern": r"\b26\b", "max_results": 5}))
        first = json.loads(tool_msgs[-1].text())["data"]["matches"][0]
        return submit(f"化合物 26 首次出现在第 {first['page'] + 1} 页的摘要。", [{"ref": first["node_id"]}])

    res = await build_ir_qa_agent(src, FakeLLM(step), ListTracer()).ask(
        "化合物 26 在正文中第一次出现在哪里？"
    )
    assert res.stop_reason == "answered"
    assert res.evidence[0]["node_id"] == REF + "p000.b007"
    assert res.evidence[0]["regions"][0]["page"] == 0


async def test_resume_after_crash(src: IrDocumentSource) -> None:
    saver = InMemorySaver()
    script = _script()
    crash = FakeLLM([script[0], script[1], RuntimeError("worker killed")])
    agent = build_ir_qa_agent(src, crash, ListTracer(), checkpointer=saver)
    with pytest.raises(RuntimeError):
        await agent.ask("Table 3 的标题？", run_id="run_crash")
    # 新进程：同一个 checkpointer、同一个 run_id，只需要再调用一次模型
    fresh = FakeLLM([script[2]])
    again = build_ir_qa_agent(src, fresh, ListTracer(), checkpointer=saver)
    res = await again.resume("run_crash")
    assert res.stop_reason == "answered" and len(fresh.requests) == 1
    assert res.tool_calls == 2 and res.model_calls == 3
    done = await again.resume("run_crash")  # 已结束：直接返回
    assert done.stop_reason == "answered" and len(fresh.requests) == 1
    with pytest.raises(KeyError):
        await again.resume("run_unknown")


def test_resolve_evidence_skips_unknown(src: IrDocumentSource) -> None:
    ev = resolve_evidence(
        src, {"citations": [{"ref": "p999.b000"}, {"ref": "p000.b007", "quote": "dopamine replacement"}]}
    )
    assert len(ev) == 1 and ev[0].char_span is not None
