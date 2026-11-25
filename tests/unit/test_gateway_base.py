from __future__ import annotations

from pydantic import BaseModel

from drugpaper.gateway.base import (
    ChatRequest,
    ChatResponse,
    ImagePart,
    Message,
    ModelTimeout,
    SchemaViolation,
    TextPart,
    ToolCall,
    Usage,
)


class Out(BaseModel):
    x: int


def test_message_roundtrip() -> None:
    m = Message(
        role="assistant",
        content=[TextPart(text="hi"), ImagePart(data_b64="AAAA", mime="image/jpeg")],
        tool_calls=[ToolCall(id="c1", name="grep", arguments={"pattern": "D1"})],
    )
    again = Message.model_validate_json(m.model_dump_json())
    assert again == m
    assert again.text() == "hi"
    assert isinstance(again.content, list) and isinstance(again.content[1], ImagePart)


def test_tool_message_roundtrip() -> None:
    m = Message(role="tool", content='{"ok": true}', tool_call_id="c1", name="grep")
    assert Message.model_validate(m.model_dump()) == m


def test_request_response_models() -> None:
    req = ChatRequest(messages=[Message(role="user", content="q")], response_schema=Out)
    assert req.response_schema is Out
    resp = ChatResponse(content='{"x":1}', parsed=Out(x=1), usage=Usage(input_tokens=3, output_tokens=2))
    assert resp.tool_calls == []
    total = resp.usage + Usage(input_tokens=1, image_count=1)
    assert (total.input_tokens, total.output_tokens, total.image_count) == (4, 2, 1)


def test_exceptions() -> None:
    assert ModelTimeout.retryable
    e = SchemaViolation("raw", ["x: field required"])
    assert e.raw == "raw" and e.errors == ["x: field required"]
    assert "field required" in str(e)
    assert not SchemaViolation.retryable
