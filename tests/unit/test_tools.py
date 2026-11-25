from __future__ import annotations

import asyncio

import pytest
from pydantic import BaseModel, Field

from drugpaper.gateway.base import ToolCall
from drugpaper.tools.protocol import ToolContext, ToolResult, compact_json_schema, tool
from drugpaper.tools.registry import TRUNCATION_NOTICE, ToolRegistrationError, ToolRegistry

CTX = ToolContext(run_id="r1", trace_id="t1")


class EchoIn(BaseModel):
    text: str = Field(description="要回显的文本")
    times: int = 1


class SleepIn(BaseModel):
    seconds: float


class Empty(BaseModel):
    pass


class BigIn(BaseModel):
    n: int


@tool("echo", "回显文本")
async def echo(ctx: ToolContext, args: EchoIn) -> ToolResult:
    return ToolResult(ok=True, data={"text": args.text * args.times}, refs=["p000.b001"])


@tool("sleep", "睡眠", timeout_s=0.05)
async def sleep(ctx: ToolContext, args: SleepIn) -> dict[str, float]:
    await asyncio.sleep(args.seconds)
    return {"slept": args.seconds}


@tool("boom", "抛异常")
async def boom(ctx: ToolContext, args: Empty) -> ToolResult:
    raise RuntimeError("内部错误")


@tool("big", "大输出", max_output_chars=200)
async def big(ctx: ToolContext, args: BigIn) -> ToolResult:
    ids = [f"p{i:03d}.b000" for i in range(args.n)]
    return ToolResult(ok=True, data={"ids": ids}, refs=ids)


def reg() -> ToolRegistry:
    return ToolRegistry([echo, sleep, boom, big])


def call(name: str, **args: object) -> ToolCall:
    return ToolCall(id="c1", name=name, arguments=dict(args))


def test_register_duplicate_raises() -> None:
    r = reg()
    with pytest.raises(ToolRegistrationError, match="重名"):
        r.register(echo)


def test_get_unknown_raises_keyerror() -> None:
    with pytest.raises(KeyError):
        reg().get("nope")


async def test_invoke_ok() -> None:
    res = await reg().invoke(CTX, call("echo", text="ab", times=2))
    assert res.ok and res.data == {"text": "abab"} and res.refs == ["p000.b001"]


async def test_unknown_tool() -> None:
    res = await reg().invoke(CTX, call("nope"))
    assert not res.ok and res.error == "unknown tool: nope"


async def test_not_in_allowed_set_is_unknown() -> None:
    res = await reg().invoke(CTX, call("echo", text="x"), allowed={"sleep"})
    assert not res.ok and res.error == "capability not loaded: echo"


async def test_argument_validation_error() -> None:
    res = await reg().invoke(CTX, call("echo", times="x"))
    assert not res.ok
    assert res.error and res.error.startswith("参数校验失败")
    assert "text" in res.error and "times" in res.error
    assert "Traceback" not in res.error


async def test_invalid_json_arguments() -> None:
    bad = ToolCall(id="c1", name="echo", raw_arguments="{x", arguments_error="参数不是合法 JSON")
    res = await reg().invoke(CTX, bad)
    assert not res.ok and res.error == "参数不是合法 JSON"


async def test_timeout() -> None:
    res = await reg().invoke(CTX, call("sleep", seconds=1))
    assert not res.ok and res.error == "timeout"


async def test_dict_return_wrapped() -> None:
    res = await reg().invoke(CTX, call("sleep", seconds=0))
    assert res.ok and res.data == {"slept": 0.0}


async def test_exception_converted() -> None:
    res = await reg().invoke(CTX, call("boom"))
    assert not res.ok and res.error == "RuntimeError: 内部错误"


async def test_truncation() -> None:
    res = await reg().invoke(CTX, call("big", n=100))
    assert res.ok and res.truncated
    assert res.data is not None
    text = res.data["truncated_text"]
    assert text.endswith(TRUNCATION_NOTICE)
    assert len(text) <= 200 + len(TRUNCATION_NOTICE)
    assert 0 < len(res.refs) < 100  # 只保留截断后仍可见的 refs
    assert all(r in text for r in res.refs)


def test_schema_generation() -> None:
    schema = echo.spec.to_schema()
    assert schema.name == "echo" and schema.description == "回显文本"
    p = schema.parameters
    assert p["type"] == "object"
    assert p["required"] == ["text"]
    assert p["properties"]["text"] == {"description": "要回显的文本", "type": "string"}
    assert "title" not in p and "title" not in p["properties"]["times"]
    empty = boom.spec.to_schema().parameters
    assert empty["type"] == "object" and empty["properties"] == {}


def test_compact_schema_keeps_property_named_title() -> None:
    s = {"title": "X", "properties": {"title": {"type": "string", "title": "Title"}}}
    assert compact_json_schema(s) == {"properties": {"title": {"type": "string"}}}


def test_decorator_rejects_bad_signature() -> None:
    with pytest.raises(TypeError):

        @tool("bad", "bad")
        async def bad(ctx: ToolContext) -> ToolResult:  # 缺少 args
            return ToolResult(ok=True)

    with pytest.raises(TypeError):

        @tool("sync", "sync")
        def sync(ctx: ToolContext, args: Empty) -> ToolResult:  # type: ignore[arg-type]
            return ToolResult(ok=True)


def test_tool_result_message_content() -> None:
    assert ToolResult(ok=False, error="timeout").to_message_content() == '{"ok":false,"error":"timeout"}'
