"""Primary transport retry exhaustion precedes opt-in model failover."""

from __future__ import annotations

import json
from pathlib import Path

import httpx2
import pytest

from drugpaper.config.models_file import ResolvedModel
from drugpaper.gateway.base import ChatRequest, Message, ModelBadResponse, ModelUnavailable
from drugpaper.gateway.openai_compat import OpenAICompatClient
from drugpaper.gateway.task_router import RoutedModelClient, TaskRouter
from tests.fakes.mock_http import MockChatServer

MODELS = Path(__file__).resolve().parents[2] / "config/models.yaml"
ENV = {"LLM_BASE_URL": "https://model.test/v1", "LLM_API_KEY": "test", "LLM_MODEL": "gpt-6-luna"}


def completed(model: str) -> httpx2.Response:
    message = {
        "type": "message",
        "id": "msg_test",
        "role": "assistant",
        "status": "completed",
        "content": [{"type": "output_text", "text": "OK", "annotations": []}],
    }
    events = [
        {"type": "response.output_item.done", "output_index": 0, "item": message},
        {
            "type": "response.completed",
            "response": {
                "id": "resp_test",
                "object": "response",
                "created_at": 0,
                "model": model,
                "status": "completed",
                "output": [message],
                "usage": {"input_tokens": 4, "output_tokens": 1, "total_tokens": 5},
            },
        },
    ]
    return httpx2.Response(
        200,
        headers={"content-type": "text/event-stream"},
        content="".join(f"event: {e['type']}\ndata: {json.dumps(e)}\n\n" for e in events).encode(),
    )


def routed(server: MockChatServer) -> RoutedModelClient:
    def factory(spec: ResolvedModel) -> OpenAICompatClient:
        return OpenAICompatClient(
            spec, retry_base_delay_s=0, retry_jitter_s=0, http_client=server.http_client()
        )

    return RoutedModelClient(TaskRouter.from_file(MODELS, ENV), factory)


async def test_three_primary_failures_then_backup_preserves_task_request() -> None:
    server = MockChatServer(
        *[httpx2.Response(503, json={"error": {"message": "unavailable"}}) for _ in range(3)],
        completed("gpt-5.6-terra"),
    )
    client = routed(server)
    try:
        response = await client.chat(
            ChatRequest(messages=[Message(role="user", content="question")], max_tokens=123), task="chat"
        )
        assert response.content == "OK" and response.model == "gpt-5.6-terra"
        assert [server.body(i)["model"] for i in range(4)] == ["gpt-6-luna"] * 3 + ["gpt-5.6-terra"]
        assert server.body(3)["max_output_tokens"] == 123
        assert server.body(3)["input"] == server.body(0)["input"]
        assert client.calls == [("chat", "text_llm"), ("chat", "text_llm_fallback")]
    finally:
        await client.aclose()


async def test_success_during_primary_retries_never_uses_backup() -> None:
    server = MockChatServer(
        httpx2.Response(429, json={"error": {"message": "rate limit"}}), completed("gpt-6-luna")
    )
    client = routed(server)
    try:
        assert (await client.chat(ChatRequest(messages=[]))).model == "gpt-6-luna"
        assert server.call_count == 2 and client.calls == [("default", "text_llm")]
    finally:
        await client.aclose()


async def test_authentication_failure_does_not_retry_or_failover() -> None:
    server = MockChatServer(httpx2.Response(401, json={"error": {"message": "invalid credential"}}))
    client = routed(server)
    try:
        with pytest.raises(ModelBadResponse):
            await client.chat(ChatRequest(messages=[]))
        assert server.call_count == 1 and len(client.calls) == 1
    finally:
        await client.aclose()


async def test_both_models_exhausted_has_finite_six_attempt_bound() -> None:
    server = MockChatServer(httpx2.Response(503, json={"error": {"message": "unavailable"}}))
    client = routed(server)
    try:
        with pytest.raises(ModelUnavailable):
            await client.chat(ChatRequest(messages=[]))
        assert server.call_count == 6
    finally:
        await client.aclose()


def test_only_text_stages_opt_in_and_text_provider_bypasses_environment_proxy() -> None:
    router = TaskRouter.from_file(MODELS, ENV)
    assert [s.model_name for s in router.candidates("reader_page")] == ["gpt-6-luna", "gpt-5.6-terra"]
    assert not router.resolve("default").trust_env
    assert len(router.candidates("visual_classify")) == 1
    assert router.resolve("visual_classify").trust_env


def test_cache_identity_includes_fallback_without_credentials() -> None:
    first = TaskRouter.from_file(MODELS, ENV)
    changed = TaskRouter.from_file(MODELS, {**ENV, "LLM_FALLBACK_MODEL": "other-backup"})
    assert first.resolve("default") == changed.resolve("default")
    assert first.public_config("default") != changed.public_config("default")
    assert all("api_key" not in spec for spec in first.public_config("default")["candidates"])


def test_cli_uses_the_same_routing_for_configured_text_model() -> None:
    from drugpaper.cli import _text_client
    from drugpaper.config.settings import Settings

    router = TaskRouter.from_file(MODELS, ENV)
    client = _text_client(Settings(models_config_path=MODELS), router.resolve("default"))
    assert isinstance(client, RoutedModelClient)


async def test_debug_sink_records_each_real_transport_attempt_without_auth_headers() -> None:
    events = []

    async def sink(kind, payload):
        events.append((kind, payload))

    server = MockChatServer(
        httpx2.Response(503, json={"error": {"message": "retry"}}), completed("gpt-6-luna")
    )
    spec = TaskRouter.from_file(MODELS, ENV).resolve("default")
    client = OpenAICompatClient(
        spec, retry_base_delay_s=0, retry_jitter_s=0, http_client=server.http_client(), event_sink=sink
    )
    try:
        assert (await client.chat(ChatRequest(messages=[Message(role="user", content="OK")]))).content == "OK"
        requests = [data for kind, data in events if kind == "transport_request"]
        assert [r["attempt"] for r in requests] == [1, 2]
        assert requests[0]["body"] == server.body(0)
        assert "transport_error" in [kind for kind, _ in events]
        assert "transport_response" in [kind for kind, _ in events]
        assert "api_key" not in json.dumps(events) and "Authorization" not in json.dumps(events)
    finally:
        await client.aclose()
