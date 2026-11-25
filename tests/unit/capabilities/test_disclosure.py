from __future__ import annotations

import pytest
from pydantic import BaseModel

from drugpaper.agent.config import AgentConfig
from drugpaper.agent.runtime import AgentRuntime
from drugpaper.agent.state import initial_state
from drugpaper.capabilities.index import META_NAMES, Capability, CapabilityIndex
from drugpaper.capabilities.meta_tools import make_meta_tools
from drugpaper.capabilities.router import CapabilityRouter, DisclosureContext
from drugpaper.capabilities.toolset import DisclosureToolset
from drugpaper.tools.protocol import ToolContext, ToolResult, tool
from drugpaper.tools.registry import ToolRegistry
from tests.fakes.fake_llm import FakeLLM, ListTracer, call, submit, tools


class Empty(BaseModel):
    pass


@tool("extra", "额外字数统计工具")
async def extra(ctx, args: Empty):
    return ToolResult(ok=True, data={"words": 3})


def setup():
    registry = ToolRegistry([extra])
    index = CapabilityIndex(None, registry)
    toolset = DisclosureToolset(registry, [], index)
    for t in make_meta_tools(index, toolset):
        registry.register(t)
    return registry, index, toolset


async def test_find_load_invoke_next_round_and_reject_unloaded():
    registry, index, toolset = setup()
    llm = FakeLLM(
        [
            tools(call("extra")),
            tools(call("find_capabilities", {"query": "字数"})),
            tools(call("load_capability", {"name": "extra"})),
            tools(call("extra")),
            submit("3"),
        ]
    )
    runtime = AgentRuntime(
        AgentConfig(name="disclosure", system_prompt="测试", toolset=toolset), llm, registry, ListTracer()
    )
    result = await runtime.run("统计字数", ToolContext(run_id="run_disc", trace_id="tr"))
    assert result.stop_reason == "answered"
    assert (
        "extra" not in llm.tool_names(0) and "extra" not in llm.tool_names(2) and "extra" in llm.tool_names(3)
    )
    assert "capability not loaded" in llm.requests[1].messages[-1].text()


async def test_same_round_load_does_not_authorize_invocation():
    registry, index, toolset = setup()
    llm = FakeLLM([tools(call("load_capability", {"name": "extra"}), call("extra")), submit("done")])
    runtime = AgentRuntime(
        AgentConfig(name="disclosure", system_prompt="测试", toolset=toolset), llm, registry, ListTracer()
    )
    await runtime.run("x", ToolContext(run_id="run_same", trace_id="tr"))
    assert "capability not loaded" in llm.requests[1].messages[-1].text()


async def test_lru_idle_and_per_run_isolation():
    registry, index, toolset = setup()
    ctx = ToolContext(run_id="run", trace_id="tr", capability_state={"step": 0})
    for i in range(7):
        name = f"extra{i}"
        index.entries[name] = Capability(name, "tool", "desc", "long", tools=["extra"])
        ctx.capability_state["step"] = i
        result = await registry.invoke(ctx, call("load_capability", {"name": name}))
        assert result.ok
    assert len(ctx.capability_state["loaded"]) == 6 and "extra0" not in ctx.capability_state["loaded"]
    state = initial_state("run", "x", {})
    state["capability_state"] = ctx.capability_state
    state["steps"] = 10
    assert toolset.prepare(state)["loaded"] == {}
    other = initial_state("other", "x", {})
    assert {t.spec.name for t in toolset.tools_for(other)} == META_NAMES


async def test_cards_not_redisclosed_and_visibility_enforced():
    registry, index, toolset = setup()
    ctx = ToolContext(run_id="run", trace_id="tr")
    first = await registry.invoke(ctx, call("describe_capability", {"name": "extra"}))
    second = await registry.invoke(ctx, call("describe_capability", {"name": "extra"}))
    assert first.data["l1"] and second.data["already_disclosed"]
    index.entries["private"] = Capability("private", "tool", "secret", "secret", audiences=["codegen"])
    assert not (await registry.invoke(ctx, call("load_capability", {"name": "private"}))).ok


@pytest.mark.parametrize("budget", [0, 20, 100, 1500])
def test_router_failed_check_exact_filter_budget_and_loaded_exclusion(budget):
    registry, index, _ = setup()
    index.entries["axis"] = Capability(
        "axis",
        "tool",
        "轴校准",
        "详细信息" * 60,
        triggers={"check_codes": ["C_AXIS_RESIDUAL"], "intents": ["轴"]},
    )
    index.entries["other"] = Capability("other", "tool", "无关", "详细", triggers={"intents": ["轴"]})
    disclosure = CapabilityRouter(index).preselect(
        DisclosureContext(failed_checks=["C_AXIS_RESIDUAL"], intents=["轴"], token_budget=budget)
    )
    assert {c.name for c in disclosure.candidates} == {"axis"}
    assert disclosure.total_tokens <= budget
    assert (
        CapabilityRouter(index)
        .preselect(DisclosureContext(failed_checks=["C_AXIS_RESIDUAL"], loaded=["axis"]))
        .items
        == []
    )


async def test_find_natural_language_keywords_and_unmatched_hint():
    registry, index, _ = setup()
    index.entries["molecule_detect"] = Capability(
        "molecule_detect", "skill", "检测分子并建立稳定节点", "图像编号"
    )
    index.entries["molecule_recognize"] = Capability(
        "molecule_recognize", "skill", "识别分子结构并保存候选", "mol_id"
    )
    index.entries["mol_backfill"] = Capability("mol_backfill", "skill", "确定性分子回填", "完整结构")
    index.entries["hidden_detect"] = Capability(
        "hidden_detect", "skill", "检测分子", "识别", audiences=["codegen"]
    )
    ctx = ToolContext(run_id="search", trace_id="search")
    query = "图像分子结构检测 识别 mol_id 编号图 最终确定性回填 artifact_inputs 文献"
    result = await registry.invoke(ctx, call("find_capabilities", {"query": query}))
    names = {c["name"] for c in result.data["capabilities"]}
    assert {"molecule_detect", "molecule_recognize", "mol_backfill"} <= names
    assert "hidden_detect" not in names
    exact = await registry.invoke(ctx, call("find_capabilities", {"query": "molecule_detect"}))
    assert exact.data["capabilities"][0]["name"] == "molecule_detect"
    absent = await registry.invoke(ctx, call("find_capabilities", {"query": "qqqq_unmatched"}))
    assert not absent.data["capabilities"] and "不代表能力不存在" in absent.data["hint"]
