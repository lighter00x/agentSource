"""Responses 工具续接与兼容网关 SSE 的回归测试。"""

from __future__ import annotations

import json
from typing import Any

import httpx2
import pytest

from drugpaper.config.models_file import ResolvedModel
from drugpaper.gateway.base import ChatRequest, Message, ModelBadResponse, ToolSchema
from drugpaper.gateway.openai_compat import OpenAICompatClient
from tests.fakes.mock_http import MockChatServer


def response(items: list[dict[str, Any]], *, status: str = "completed") -> httpx2.Response:
    record = {
        "id": "resp_test",
        "object": "response",
        "created_at": 0,
        "model": "gpt-6-luna",
        "status": status,
        "output": [],
        "usage": {"input_tokens": 12, "output_tokens": 8, "total_tokens": 20},
        "parallel_tool_calls": True,
        "tool_choice": "auto",
        "tools": [],
    }
    events = [
        {"type": "response.output_item.done", "output_index": i, "item": item} for i, item in enumerate(items)
    ]
    events.append({"type": f"response.{status}", "response": record})
    return httpx2.Response(
        200,
        headers={"content-type": "text/event-stream"},
        content="".join(
            f"event: {event['type']}\ndata: {json.dumps(event)}\n\n" for event in events
        ).encode(),
    )


def client(server: MockChatServer) -> OpenAICompatClient:
    model = ResolvedModel(
        base_url="https://llm.test/v1",
        api_key="test",
        model_name="gpt-6-luna",
        api="responses",
        extra_body={"reasoning": {"effort": "medium"}},
    )
    return OpenAICompatClient(model, http_client=server.http_client(), retry_base_delay_s=0, retry_jitter_s=0)


def message(text: str) -> dict[str, Any]:
    return {
        "id": "msg_test",
        "type": "message",
        "status": "completed",
        "role": "assistant",
        "content": [{"type": "output_text", "text": text, "annotations": []}],
    }


async def test_tool_output_and_reasoning_survive_stateless_replay() -> None:
    reasoning = {"id": "rs_test", "type": "reasoning", "summary": [], "encrypted_content": "opaque"}
    call = {
        "id": "fc_test",
        "type": "function_call",
        "status": "completed",
        "call_id": "call_test",
        "name": "grep",
        "arguments": '{"pattern":"UCM-1306"}',
    }
    server = MockChatServer(response([reasoning, call]), response([message("化合物 26")]))
    model = client(server)
    try:
        first = await model.chat(
            ChatRequest(
                messages=[Message(role="user", content="查找编号")],
                tools=[ToolSchema(name="grep", description="检索", parameters={"type": "object"})],
            )
        )
        assert first.tool_calls[0].id == "call_test"
        assert first.tool_calls[0].arguments == {"pattern": "UCM-1306"}
        assert first.response_items[0]["encrypted_content"] == "opaque"
        second = await model.chat(
            ChatRequest(
                messages=[
                    Message(role="user", content="查找编号"),
                    Message(
                        role="assistant", tool_calls=first.tool_calls, response_items=first.response_items
                    ),
                    Message(role="tool", tool_call_id="call_test", content="UCM-1306 (26)"),
                ]
            )
        )
        assert second.content == "化合物 26"
        assert second.usage.input_tokens == 12 and second.usage.output_tokens == 8
        body = server.body(0)
        assert server.requests[0].url.path == "/v1/responses"
        assert body["reasoning"] == {"effort": "medium"} and body["store"] is False
        assert "temperature" not in body and "max_tokens" not in body
        replay = server.body(1)["input"]
        assert [item.get("type") for item in replay] == [
            None,
            "reasoning",
            "function_call",
            "function_call_output",
        ]
        assert replay[-1]["call_id"] == "call_test"
    finally:
        await model.aclose()


async def test_json_schema_output_is_parsed() -> None:
    from tests.unit.test_openai_compat import Answer

    server = MockChatServer(response([message('{"answer":"ok","score":1}')]))
    model = client(server)
    try:
        result = await model.chat(
            ChatRequest(messages=[Message(role="user", content="JSON")], response_schema=Answer)
        )
        assert result.parsed is not None and result.parsed.model_dump() == {"answer": "ok", "score": 1}
        assert server.body(0)["text"]["format"]["type"] == "json_schema"
    finally:
        await model.aclose()


@pytest.mark.parametrize("items,status", [([], "completed"), ([message("partial")], "incomplete")])
async def test_empty_or_incomplete_stream_is_rejected(items: list[dict[str, Any]], status: str) -> None:
    model = client(MockChatServer(response(items, status=status)))
    try:
        with pytest.raises(ModelBadResponse):
            await model.chat(ChatRequest(messages=[Message(role="user", content="q")]))
    finally:
        await model.aclose()


async def test_responses_transmits_image_with_structured_schema() -> None:
    from drugpaper.gateway.base import ImagePart, TextPart
    from tests.unit.test_openai_compat import Answer

    server = MockChatServer(response([message('{"answer":"ok","score":1}')]))
    model = client(server)
    try:
        result = await model.chat(
            ChatRequest(
                messages=[
                    Message(
                        role="user",
                        content=[
                            TextPart(text="synthetic image"),
                            ImagePart(data_b64="aW1hZ2U="),
                        ],
                    )
                ],
                response_schema=Answer,
            )
        )
        assert result.parsed.answer == "ok"
        body = server.body(0)
        assert body["input"][0]["content"][1] == {
            "type": "input_image",
            "image_url": "data:image/png;base64,aW1hZ2U=",
        }
        assert body["text"]["format"]["type"] == "json_schema"
    finally:
        await model.aclose()


@pytest.mark.parametrize(
    "code,message_text",
    [("server_error", "Upstream service is unavailable."), (None, "Upstream service is unavailable.")],
)
async def test_transient_sse_gateway_error_retries_identical_request(code, message_text) -> None:
    failure = {"type": "error", "code": code, "message": message_text}
    transient = httpx2.Response(
        200,
        headers={"content-type": "text/event-stream"},
        content=f"event: error\ndata: {json.dumps(failure)}\n\n".encode(),
    )
    server = MockChatServer(transient, response([message("recovered")]))
    model = client(server)
    try:
        result = await model.chat(ChatRequest(messages=[Message(role="user", content="q")]))
        assert result.content == "recovered"
        assert server.call_count == 2
        assert server.body(0) == server.body(1)
    finally:
        await model.aclose()


async def test_nontransient_sse_gateway_error_is_not_retried() -> None:
    failure = {"type": "error", "code": "invalid_request_error", "message": "Unsupported input"}
    server = MockChatServer(
        httpx2.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=f"event: error\ndata: {json.dumps(failure)}\n\n".encode(),
        )
    )
    model = client(server)
    try:
        with pytest.raises(ModelBadResponse):
            await model.chat(ChatRequest(messages=[Message(role="user", content="q")]))
        assert server.call_count == 1
    finally:
        await model.aclose()
