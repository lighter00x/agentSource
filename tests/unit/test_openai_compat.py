from __future__ import annotations

import base64
from typing import Any

import httpx2
import pytest
from pydantic import BaseModel

from drugpaper.config.models_file import ResolvedModel
from drugpaper.gateway.base import (
    ChatRequest,
    ImagePart,
    Message,
    ModelBadResponse,
    ModelTimeout,
    ModelUnavailable,
    SchemaViolation,
    TextPart,
    ToolCall,
    ToolSchema,
)
from drugpaper.gateway.openai_compat import OpenAICompatClient, normalize_base_url
from tests.fakes.mock_http import MockChatServer


class Answer(BaseModel):
    answer: str
    score: int


def make_client(server: MockChatServer, **kw: Any) -> OpenAICompatClient:
    rm = ResolvedModel(
        base_url="http://llm.test",
        api_key="sk-test",
        model_name=kw.pop("model_name", "qwen-test"),
        timeout_s=5,
        thinking_toggle=kw.pop("thinking_toggle", "chat_template_kwargs"),
        structured_output=kw.pop("structured_output", "json_schema"),
    )
    return OpenAICompatClient(
        rm, retry_base_delay_s=0, retry_jitter_s=0, http_client=server.http_client(), **kw
    )


def completion(content: str | None = None, tool_calls: list[dict[str, Any]] | None = None) -> httpx2.Response:
    msg: dict[str, Any] = {"role": "assistant", "content": content}
    if tool_calls:
        msg["tool_calls"] = tool_calls
    return httpx2.Response(
        200,
        json={
            "id": "cmpl-1",
            "object": "chat.completion",
            "created": 0,
            "model": "qwen-test-served",
            "choices": [{"index": 0, "message": msg, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 11, "completion_tokens": 7, "total_tokens": 18},
        },
    )


def error(status: int, message: str) -> httpx2.Response:
    return httpx2.Response(status, json={"error": {"message": message}})


def tc(name: str, args: str, id_: str = "call_1") -> dict[str, Any]:
    return {"id": id_, "type": "function", "function": {"name": name, "arguments": args}}


def req(text: str = "你好", **kw: Any) -> ChatRequest:
    return ChatRequest(
        messages=[Message(role="system", content="sys"), Message(role="user", content=text)], **kw
    )


@pytest.fixture(autouse=True)
def _reset_cache() -> None:
    OpenAICompatClient.reset_structured_cache()


def test_normalize_base_url() -> None:
    assert normalize_base_url("http://h:8000") == "http://h:8000/v1"
    assert normalize_base_url("http://h:8000/v1/") == "http://h:8000/v1"


async def test_plain_text() -> None:
    server = MockChatServer(completion("你好！"))
    resp = await make_client(server).chat(req())
    assert resp.content == "你好！"
    assert (resp.usage.input_tokens, resp.usage.output_tokens) == (11, 7)
    assert resp.model == "qwen-test-served"
    assert str(server.requests[0].url) == "http://llm.test/v1/chat/completions"
    body = server.body(0)
    assert body["chat_template_kwargs"] == {"enable_thinking": False}  # thinking=False → 关闭思考输出
    assert body["messages"][0] == {"role": "system", "content": "sys"}


async def test_thinking_toggle_none_and_think_tag_stripped() -> None:
    server = MockChatServer(completion("<think>xx</think>答案"))
    resp = await make_client(server, thinking_toggle="none").chat(req())
    assert resp.content == "答案"
    assert "chat_template_kwargs" not in server.body(0)


async def test_tool_calls_parsed_and_tools_sent() -> None:
    server = MockChatServer(completion(None, [tc("grep", '{"pattern": "UCM-1306"}')]))
    tools = [ToolSchema(name="grep", description="search", parameters={"type": "object", "properties": {}})]
    resp = await make_client(server).chat(req(tools=tools))
    assert resp.tool_calls == [ToolCall(id="call_1", name="grep", arguments={"pattern": "UCM-1306"})]
    sent = server.body(0)["tools"][0]
    assert sent["type"] == "function" and sent["function"]["name"] == "grep"


async def test_invalid_tool_arguments_marked_not_raised() -> None:
    server = MockChatServer(completion(None, [tc("get_block", "{bad json")]))
    resp = await make_client(server).chat(req())
    call = resp.tool_calls[0]
    assert call.arguments == {}
    assert call.raw_arguments == "{bad json"
    assert call.arguments_error and "JSON" in call.arguments_error


async def test_500_twice_then_success() -> None:
    server = MockChatServer(error(500, "boom"), error(502, "boom"), completion("ok"))
    resp = await make_client(server).chat(req())
    assert resp.content == "ok"
    assert server.call_count == 3


async def test_500_exhausted_raises_unavailable() -> None:
    server = MockChatServer(error(503, "down"))
    with pytest.raises(ModelUnavailable):
        await make_client(server).chat(req())
    assert server.call_count == 3


async def test_429_is_retried() -> None:
    server = MockChatServer(error(429, "slow down"), completion("ok"))
    resp = await make_client(server).chat(req())
    assert resp.content == "ok"
    assert server.call_count == 2


async def test_4xx_not_retried() -> None:
    server = MockChatServer(error(401, "bad key"))
    with pytest.raises(ModelBadResponse):
        await make_client(server).chat(req())
    assert server.call_count == 1


async def test_timeout_raises_model_timeout() -> None:
    server = MockChatServer(httpx2.ReadTimeout("slow"))
    with pytest.raises(ModelTimeout):
        await make_client(server).chat(req())
    assert server.call_count == 3


async def test_structured_json_schema() -> None:
    server = MockChatServer(completion('```json\n{"answer": "26", "score": 1}\n```'))
    resp = await make_client(server).chat(req(response_schema=Answer))
    assert isinstance(resp.parsed, Answer) and resp.parsed.answer == "26"
    rf = server.body(0)["response_format"]
    assert rf["type"] == "json_schema"
    assert rf["json_schema"]["name"] == "Answer"
    assert rf["json_schema"]["strict"] is False


async def test_json_schema_unsupported_downgrades_and_caches() -> None:
    server = MockChatServer(
        error(400, "response_format json_schema is not supported"),
        completion('{"answer": "26", "score": 2}'),
        completion('{"answer": "D1", "score": 3}'),
    )
    client = make_client(server)
    resp = await client.chat(req(response_schema=Answer))
    assert isinstance(resp.parsed, Answer) and resp.parsed.score == 2
    second = server.body(1)
    assert second["response_format"] == {"type": "json_object"}
    assert "JSON Schema" in second["messages"][0]["content"]  # 系统消息末尾追加了 Schema 说明
    # 降级结果按模型名缓存：下一次请求直接用 json_object
    await client.chat(req(response_schema=Answer))
    assert server.body(2)["response_format"] == {"type": "json_object"}
    assert server.call_count == 3


async def test_schema_violation_repair_then_success() -> None:
    server = MockChatServer(completion('{"answer": "26"}'), completion('{"answer": "26", "score": 5}'))
    resp = await make_client(server).chat(req(response_schema=Answer))
    assert isinstance(resp.parsed, Answer) and resp.parsed.score == 5
    assert "不符合 Schema" in server.body(1)["messages"][-1]["content"]
    assert resp.usage.input_tokens == 22  # 两次调用的 usage 合计


async def test_schema_violation_twice_raises() -> None:
    server = MockChatServer(completion("not json"))
    with pytest.raises(SchemaViolation) as ei:
        await make_client(server).chat(req(response_schema=Answer))
    assert ei.value.raw == "not json"
    assert ei.value.errors
    assert server.call_count == 2


async def test_image_part_and_tool_messages_conversion() -> None:
    server = MockChatServer(completion("ok"))
    png = base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"0" * 32).decode()
    messages = [
        Message(role="user", content=[TextPart(text="看图"), ImagePart(data_b64=png, mime="image/jpeg")]),
        Message(role="assistant", tool_calls=[ToolCall(id="c1", name="grep", arguments={"pattern": "x"})]),
        Message(role="tool", tool_call_id="c1", name="grep", content='{"ok": true}'),
    ]
    resp = await make_client(server).chat(ChatRequest(messages=messages))
    assert resp.usage.image_count == 1
    sent = server.body(0)["messages"]
    assert sent[0]["content"][1]["image_url"]["url"].startswith("data:image/png;base64,")  # 按实际格式识别
    assert sent[1]["tool_calls"][0]["function"]["arguments"] == '{"pattern":"x"}'
    assert sent[2] == {"role": "tool", "tool_call_id": "c1", "content": '{"ok": true}'}


async def test_dotted_tool_wire_alias_round_trip() -> None:
    import hashlib

    name = "markush.substitute"
    wire = "dp_wire_" + hashlib.sha256(name.encode()).hexdigest()[:32]
    server = MockChatServer(
        completion(
            tool_calls=[{"id": "tc1", "type": "function", "function": {"name": wire, "arguments": "{}"}}]
        ),
        completion("done"),
    )
    client = make_client(server)
    try:
        tool = ToolSchema(name=name, description="拼接", parameters={"type": "object"})
        response = await client.chat(
            ChatRequest(messages=[Message(role="user", content="拼接")], tools=[tool])
        )
        assert response.tool_calls[0].name == name
        assert server.body(0)["tools"][0]["function"]["name"] == wire
        await client.chat(
            ChatRequest(
                messages=[
                    Message(role="assistant", tool_calls=response.tool_calls),
                    Message(role="tool", tool_call_id="tc1", content="ok"),
                ],
                tools=[],
            )
        )
        assert server.body(1)["messages"][0]["tool_calls"][0]["function"]["name"] == wire
        assert tool.name == name
    finally:
        await client.aclose()


async def test_configured_exponential_retry_records_waits(monkeypatch: pytest.MonkeyPatch) -> None:
    import drugpaper.gateway.openai_compat as gateway

    waits: list[float] = []
    events: list[tuple[str, Any]] = []

    async def sleep(seconds: float) -> None:
        waits.append(float(seconds))

    async def sink(kind: str, data: Any) -> None:
        events.append((kind, data))

    retrying = gateway.AsyncRetrying

    def retry_factory(**kwargs: Any) -> Any:
        return retrying(sleep=sleep, **kwargs)

    monkeypatch.setattr(gateway, "AsyncRetrying", retry_factory)
    server = MockChatServer(error(503, "busy"), error(503, "busy"), completion("ok"))
    model = ResolvedModel(
        base_url="http://llm.test",
        api_key="test",
        model_name="test",
        timeout_s=240,
        retry_timeout_multiplier=2,
        retry_timeout_max_s=600,
        retry_base_delay_s=5,
        retry_max_delay_s=60,
        retry_jitter_s=0,
    )
    client = OpenAICompatClient(model, http_client=server.http_client(), event_sink=sink)
    try:
        assert (await client.chat(req(timeout_s=240))).content == "ok"
    finally:
        await client.aclose()
    assert waits == [5, 10]
    requests = [data for kind, data in events if kind == "transport_request"]
    assert [r["timeout"] for r in requests] == [240, 480, 600]
    retries = [data for kind, data in events if kind == "transport_retry"]
    assert [r["delay_s"] for r in retries] == [5, 10]
    assert [r["next_attempt"] for r in retries] == [2, 3]


@pytest.mark.parametrize(
    "code,text,expected",
    [
        (None, "Upstream service is unavailable.", ModelUnavailable),
        ("server_error", "temporary error", ModelUnavailable),
        ("invalid_request_error", "Unsupported input", ModelBadResponse),
    ],
)
def test_sdk_sse_api_error_mapping(code, text, expected):
    import openai

    from drugpaper.gateway.openai_compat import _map_error

    error = openai.APIError(
        text, request=httpx2.Request("POST", "https://llm.test/responses"), body={"code": code}
    )
    assert isinstance(_map_error(error), expected)
