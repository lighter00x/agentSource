from __future__ import annotations

import asyncio
import itertools
import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from drugpaper.agent.config import AgentConfig
from drugpaper.agent.defaults import StaticToolset
from drugpaper.agent.runtime import BUDGET_WARNING, FALLBACK_BUDGET_TEXT, SANITIZED_WARNING, AgentRuntime
from drugpaper.agent.state import AgentAnswer, Budget
from drugpaper.agent.validators import CitationValidator
from drugpaper.docsrc.layout_json import LayoutJsonSource
from drugpaper.gateway.base import ChatRequest, ChatResponse, ModelUnavailable
from drugpaper.tools.builtin.layout_doc import LAYOUT_TOOL_NAMES, make_layout_tools
from drugpaper.tools.protocol import ToolContext, ToolResult, tool
from drugpaper.tools.registry import ToolRegistry
from drugpaper.tracing.jsonl import JsonlTracer, read_events
from tests.conftest import MINI_LAYOUT
from tests.fakes.fake_llm import FakeLLM, ListTracer, call, submit, text, tools

ABSTRACT = "p000.b007"
_run_ids = itertools.count(1)


class SlowIn(BaseModel):
    seconds: float = 1.0


class BigIn(BaseModel):
    pass


@tool("slow", "慢工具", timeout_s=0.05)
async def slow(ctx: ToolContext, args: SlowIn) -> ToolResult:
    await asyncio.sleep(args.seconds)
    return ToolResult(ok=True, data={})


@tool("big", "大输出", max_output_chars=100)
async def big(ctx: ToolContext, args: BigIn) -> ToolResult:
    return ToolResult(ok=True, data={"text": "x" * 1000})


SOURCE = LayoutJsonSource.from_file(MINI_LAYOUT)


def build(
    script: Any,
    *,
    budget: Budget | None = None,
    cite_check: bool = True,
    extra_tools: tuple[Any, ...] = (),
    tracer: Any = None,
) -> tuple[AgentRuntime, FakeLLM, Any]:
    registry = ToolRegistry([*make_layout_tools(SOURCE), *extra_tools])
    names = [*LAYOUT_TOOL_NAMES, *(t.spec.name for t in extra_tools)]
    config = AgentConfig(
        name="qa-test",
        system_prompt="你是论文阅读助手。",
        toolset=StaticToolset(registry, names),
        validators=[CitationValidator(SOURCE)] if cite_check else [],
        budget=budget or Budget(),
    )
    llm = FakeLLM(script)
    tr = tracer or ListTracer()
    return AgentRuntime(config, llm, registry, tr), llm, tr


def ctx() -> ToolContext:
    n = next(_run_ids)
    return ToolContext(run_id=f"run-{n}", trace_id=f"trace-{n}", document_id=SOURCE.document_id)


def tool_messages(req: ChatRequest) -> list[Any]:
    return [m for m in req.messages if m.role == "tool"]


def payload(m: Any) -> dict[str, Any]:
    return json.loads(m.content)


# ---------------------------------------------------------------- 用例表（执行方案 S1.11）


async def test_direct_answer() -> None:
    rt, llm, tr = build([submit("你好")], cite_check=False)
    res = await rt.run("你好", ctx())
    assert res.stop_reason == "answered"
    assert res.model_calls == 1 and res.steps == 1 and res.tool_calls == 0
    assert res.answer["answer"] == "你好"
    # 模型拿到的工具 = 文档工具 + submit_answer
    assert llm.tool_names(0) == [*LAYOUT_TOOL_NAMES, "submit_answer"]
    assert tr.kinds() == ["run_start", "model_call", "validation", "run_end"]


async def test_search_then_answer() -> None:
    script = [
        tools(call("grep", {"pattern": "UCM-1306"})),
        tools(call("get_block", {"block_id": ABSTRACT})),
        submit("UCM-1306 是化合物 26。", [{"ref": ABSTRACT, "quote": "UCM-1306 (26)"}]),
    ]
    rt, llm, tr = build(script)
    res = await rt.run("UCM-1306 对应论文中的哪个化合物编号？", ctx())
    assert res.stop_reason == "answered", res.warnings
    assert [p["name"] for p in tr.of("tool_call")] == ["grep", "get_block"]
    assert ABSTRACT in res.seen_refs
    assert res.answer["citations"] == [{"ref": ABSTRACT, "quote": "UCM-1306 (26)"}]
    assert res.answer["warnings"] == []
    assert (res.model_calls, res.tool_calls, res.steps) == (3, 2, 3)
    # 第 3 次请求的历史中包含两条工具结果
    msgs = tool_messages(llm.requests[2])
    assert [m.name for m in msgs] == ["grep", "get_block"]
    assert payload(msgs[1])["data"]["id"] == ABSTRACT
    assert res.usage.input_tokens == 30


async def test_parallel_tools() -> None:
    c1 = call("grep", {"pattern": "D1"}, id="c-grep")
    c2 = call("list_blocks", {"page": 3}, id="c-list")
    rt, llm, _ = build([tools(c1, c2), submit("ok", [{"ref": "p003.g000"}])])
    res = await rt.run("q", ctx())
    assert res.stop_reason == "answered"
    msgs = tool_messages(llm.requests[1])
    assert [(m.tool_call_id, m.name) for m in msgs] == [("c-grep", "grep"), ("c-list", "list_blocks")]
    assert all(payload(m)["ok"] for m in msgs)
    assert res.tool_calls == 2 and res.steps == 1 + 1


async def test_invalid_arguments_loop_continues() -> None:
    script = [
        tools(call("get_block", {})),  # 缺 block_id
        tools(call("get_block", {"block_id": ABSTRACT})),
        submit("摘要", [{"ref": ABSTRACT}]),
    ]
    rt, llm, tr = build(script)
    res = await rt.run("q", ctx())
    first = tr.of("tool_call")[0]
    assert first["ok"] is False and "参数校验失败" in first["error"]
    assert payload(tool_messages(llm.requests[1])[0])["ok"] is False
    assert res.stop_reason == "answered"


async def test_tool_timeout_loop_continues() -> None:
    rt, llm, tr = build(
        [tools(call("slow", {"seconds": 1})), submit("无", [])], cite_check=False, extra_tools=(slow,)
    )
    res = await rt.run("q", ctx())
    assert tr.of("tool_call")[0]["error"] == "timeout"
    assert payload(tool_messages(llm.requests[1])[0]) == {"ok": False, "error": "timeout"}
    assert res.stop_reason == "answered"


def grep_forever(req: ChatRequest) -> ChatResponse:
    names = [t.name for t in req.tools]
    if names == ["submit_answer"]:  # force_finalize 只绑定 submit_answer
        return submit("基于已有信息：化合物 26", [{"ref": ABSTRACT}], warnings=["预算用尽"])
    return tools(call("grep", {"pattern": "UCM-1306"}))


async def test_budget_exhausted_force_finalize() -> None:
    rt, llm, tr = build(grep_forever, budget=Budget(max_steps=3))
    res = await rt.run("q", ctx())
    assert res.stop_reason == "budget_exhausted"
    assert res.steps == 3 and res.tool_calls == 3
    assert res.model_calls == 4  # 3 步 + 1 次强制作答
    assert llm.tool_names(3) == ["submit_answer"]
    assert "预算已用尽" in llm.requests[3].messages[-1].text()
    assert res.answer["answer"].startswith("基于已有信息")
    assert BUDGET_WARNING in res.answer["warnings"]
    assert tr.of("budget_exhausted")


async def test_max_steps_one_ends_normally() -> None:
    rt, _, _ = build(grep_forever, budget=Budget(max_steps=1))
    res = await rt.run("q", ctx())
    assert res.stop_reason == "budget_exhausted"
    assert (res.steps, res.model_calls) == (1, 2)


async def test_force_finalize_without_answer_uses_fallback() -> None:
    def never_submit(req: ChatRequest) -> ChatResponse:
        return (
            text("我还想再查查")
            if [t.name for t in req.tools] == ["submit_answer"]
            else tools(call("grep", {"pattern": "x"}))
        )

    rt, _, _ = build(never_submit, budget=Budget(max_steps=2))
    res = await rt.run("q", ctx())
    assert res.stop_reason == "budget_exhausted"
    assert res.answer["answer"] == FALLBACK_BUDGET_TEXT
    assert BUDGET_WARNING in res.answer["warnings"]


async def test_max_model_calls_budget() -> None:
    rt, _, _ = build(grep_forever, budget=Budget(max_model_calls=2))
    res = await rt.run("q", ctx())
    assert res.stop_reason == "budget_exhausted"
    assert res.model_calls == 3  # 2 + 强制作答（不受上限约束但计数）


async def test_max_tool_calls_budget_skips_extra_calls() -> None:
    c = [call("grep", {"pattern": "D1"}) for _ in range(3)]
    rt, llm, _ = build([tools(*c), submit("ok", [{"ref": ABSTRACT}])], budget=Budget(max_tool_calls=2))
    res = await rt.run("q", ctx())
    assert res.tool_calls == 2
    msgs = tool_messages(llm.requests[1])
    assert len(msgs) == 3 and "budget exhausted" in payload(msgs[2])["error"]
    assert res.stop_reason == "budget_exhausted"


async def test_plain_text_answer_is_converted() -> None:
    converted = json.dumps({"answer": "化合物 26", "citations": []}, ensure_ascii=False)
    rt, llm, tr = build([text("答案是化合物 26"), text(converted)], cite_check=False)
    res = await rt.run("q", ctx())
    assert res.stop_reason == "answered"
    assert res.model_calls == 2  # 多 1 次结构化转换调用
    assert llm.requests[1].response_schema is AgentAnswer
    assert llm.requests[1].tools == []
    assert res.answer["answer"] == "化合物 26"
    assert [p["phase"] for p in tr.of("model_call")] == ["agent", "convert"]


async def test_invalid_citation_retry_then_sanitized() -> None:
    script = [
        tools(call("grep", {"pattern": "UCM-1306"})),
        submit("化合物 26", [{"ref": "p003.b099"}]),
        submit("化合物 26", [{"ref": "p003.b099"}, {"ref": ABSTRACT}]),
    ]
    rt, llm, tr = build(script)
    res = await rt.run("q", ctx())
    # 第一次校验失败 → 反馈：submit_answer 的 tool 消息 + 用户消息
    retry_req = llm.requests[2]
    assert retry_req.messages[-2].role == "tool" and retry_req.messages[-2].name == "submit_answer"
    assert "p003.b099" in retry_req.messages[-1].text()
    # 第二次仍错误：删除无效引用并加警告
    assert [c["ref"] for c in res.answer["citations"]] == [ABSTRACT]
    assert SANITIZED_WARNING in res.answer["warnings"]
    assert any("p003.b099" in w for w in res.answer["warnings"])
    assert res.stop_reason == "validation_failed"
    assert [p["retry"] for p in tr.of("validation")] == [True, False]


async def test_structured_conversion_fails_twice() -> None:
    rt, llm, _ = build([text("答案是 26"), text("不是 JSON"), text("还不是 JSON")], cite_check=False)
    res = await rt.run("q", ctx())
    assert res.stop_reason == "error"
    assert res.error and "结构化转换失败" in res.error
    assert len(llm.requests) == 3
    assert res.answer["answer"] == "答案是 26"  # 保留草稿文本


async def test_submit_with_other_tool_same_round() -> None:
    both = tools(
        call("get_block", {"block_id": ABSTRACT}),
        call("submit_answer", {"answer": "26", "citations": [{"ref": ABSTRACT}]}),
    )
    rt, _, tr = build([both])
    res = await rt.run("q", ctx())
    # 先执行 get_block，再校验答案（引用因此有效）
    assert tr.kinds().index("tool_call") < tr.kinds().index("validation")
    assert res.stop_reason == "answered" and res.tool_calls == 1


async def test_invalid_submit_arguments_retry() -> None:
    rt, llm, _ = build([tools(call("submit_answer", {"citations": []})), submit("好", [])], cite_check=False)
    res = await rt.run("q", ctx())
    assert "不符合 Schema" in llm.requests[1].messages[-1].text()
    assert res.stop_reason == "answered"


async def test_model_error_stops_with_error() -> None:
    rt, _, tr = build([ModelUnavailable("down")])
    res = await rt.run("q", ctx())
    assert res.stop_reason == "error"
    assert res.error and "ModelUnavailable" in res.error
    assert tr.kinds()[-1] == "run_end"


async def test_truncated_tool_result_reaches_model() -> None:
    rt, llm, tr = build([tools(call("big")), submit("x", [])], cite_check=False, extra_tools=(big,))
    await rt.run("q", ctx())
    assert tr.of("tool_call")[0]["truncated"] is True
    assert payload(tool_messages(llm.requests[1])[0])["truncated"] is True


async def test_wall_time_budget() -> None:
    async def slow_model(req: ChatRequest, *, task: str = "default") -> ChatResponse:
        await asyncio.sleep(0.05)
        return grep_forever(req)

    rt, llm, _ = build(grep_forever, budget=Budget(max_wall_time_s=0.08))
    llm.chat = slow_model  # type: ignore[method-assign]
    res = await rt.run("q", ctx())
    assert res.stop_reason == "budget_exhausted"


async def test_jsonl_trace_reconstructs_run(tmp_path: Path) -> None:
    script = [tools(call("grep", {"pattern": "UCM-1306"})), submit("26", [{"ref": ABSTRACT}])]
    tracer = JsonlTracer(tmp_path)
    rt, _, _ = build(script, tracer=tracer)
    c = ctx()
    res = await rt.run("UCM-1306 是哪个化合物？", c)
    events = read_events(tracer.path_for(c.run_id))
    assert [e["kind"] for e in events] == [
        "run_start",
        "model_call",
        "tool_call",
        "model_call",
        "validation",
        "run_end",
    ]
    assert all(e["run_id"] == c.run_id and e["trace_id"] == c.trace_id for e in events)
    mc = events[1]
    assert {"input_tokens", "output_tokens", "latency_ms", "tool_calls", "task"} <= mc.keys()
    assert events[2]["name"] == "grep" and events[2]["refs"] == 1
    assert events[-1]["stop_reason"] == res.stop_reason == "answered"
    assert all(len(v) <= 201 for e in events for v in e.values() if isinstance(v, str))


async def test_runs_are_isolated_per_run_id() -> None:
    rt, _, _ = build(lambda req: submit("ok", []), cite_check=False)
    r1, r2 = await asyncio.gather(rt.run("a", ctx()), rt.run("b", ctx()))
    assert r1.run_id != r2.run_id
    assert r1.model_calls == r2.model_calls == 1


async def test_seen_refs_only_from_ok_results() -> None:
    script = [
        tools(call("get_block", {"block_id": "p999.b000"})),
        submit("x", [{"ref": "p999.b000"}]),
        submit("x", []),
    ]
    rt, _, _ = build(script)
    res = await rt.run("q", ctx())
    assert "p999.b000" not in res.seen_refs
