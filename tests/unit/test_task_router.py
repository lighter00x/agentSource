"""S3.7：任务名路由。"""

from __future__ import annotations

import base64
from pathlib import Path
from typing import Any

import pymupdf
import pytest

from drugpaper.config.models_file import MissingEnvVarError, ResolvedModel
from drugpaper.gateway.base import ChatRequest, ChatResponse, ImagePart, Message, TextPart
from drugpaper.gateway.task_router import RoutedModelClient, TaskRouter
from drugpaper.render.images import image_size

MODELS = Path(__file__).resolve().parents[2] / "config" / "models.yaml"
ENV = {"LLM_BASE_URL": "http://llm/v1", "LLM_API_KEY": "k", "LLM_MODEL": "qwen-text"}


class Recorder:
    def __init__(self, spec: ResolvedModel) -> None:
        self.spec = spec
        self.reqs: list[ChatRequest] = []

    async def chat(self, req: ChatRequest, *, task: str = "default") -> ChatResponse:
        self.reqs.append(req)
        return ChatResponse(content="ok", model=self.spec.model_name)


def _png(w: int, h: int) -> bytes:
    pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, w, h), False)
    pix.clear_with(200)
    data: bytes = pix.tobytes("png")
    return data


def test_route_visual_classify_to_vision_model() -> None:
    env = {**ENV, "VLM_BASE_URL": "http://vlm/v1", "VLM_MODEL": "qwen-vl"}
    r = TaskRouter.from_file(MODELS, env)
    v = r.resolve("visual_classify")
    assert v.model_key == "vlm_small" and v.model_name == "qwen-vl" and v.base_url == "http://vlm/v1"
    assert "image" in v.modalities and v.image_max_side == 768 and v.stage == "classify"
    d = r.resolve("default")
    assert d.model_key == "text_llm" and d.model_name == "qwen-text"
    assert r.stage_of("figure_describe") == "describe"


def test_vision_falls_back_to_text_endpoint_vars() -> None:
    v = TaskRouter.from_file(MODELS, ENV).resolve("visual_classify")
    assert v.base_url == "http://llm/v1" and v.model_name == "qwen-text" and v.api_key == "k"


def test_missing_var_without_fallback() -> None:
    with pytest.raises(MissingEnvVarError):
        TaskRouter.from_file(MODELS, {}).resolve("default")


async def test_routed_client_merges_task_params_and_limits_images() -> None:
    made: list[Recorder] = []

    def factory(spec: ResolvedModel) -> Any:
        rec = Recorder(spec)
        made.append(rec)
        return rec

    client = RoutedModelClient(TaskRouter.from_file(MODELS, ENV), factory)
    big = base64.b64encode(_png(2000, 1000)).decode()
    req = ChatRequest(messages=[Message(role="user", content=[TextPart(text="?"), ImagePart(data_b64=big)])])
    await client.chat(req, task="visual_classify")
    sent = made[0].reqs[0]
    assert (sent.temperature, sent.max_tokens, sent.timeout_s) == (0, 512, 30)
    img = sent.messages[0].content[1]  # type: ignore[index]
    assert image_size(base64.b64decode(img.data_b64)) == (768, 384)
    # 显式设置的参数优先于任务配置
    await client.chat(
        ChatRequest(messages=[Message(role="user", content="x")], max_tokens=99), task="visual_classify"
    )
    assert made[0].reqs[1].max_tokens == 99
    # 不同模型用不同底层客户端；同一模型复用
    await client.chat(ChatRequest(messages=[Message(role="user", content="x")]), task="default")
    await client.chat(ChatRequest(messages=[Message(role="user", content="x")]), task="chat")
    assert len(made) == 2
    assert [t for t, _ in client.calls] == ["visual_classify", "visual_classify", "default", "chat"]


def test_structure_uses_existing_llm_credentials_and_keeps_vision_inputs() -> None:
    env = {**ENV, "VLM_BASE_URL": "http://vlm/v1", "VLM_MODEL": "qwen-vl"}
    router = TaskRouter.from_file(MODELS, env)
    spec = router.resolve("figure_structure")
    assert spec.base_url == ENV["LLM_BASE_URL"]
    assert spec.api_key == ENV["LLM_API_KEY"]
    assert spec.model_name == "gpt-6-luna" and spec.api == "responses"
    assert "image" in spec.modalities and spec.structured_output == "json_schema"
    assert spec.timeout_s == 240 and spec.retry_timeout_multiplier == 2
    assert spec.retry_timeout_max_s == 600 and spec.retry_base_delay_s == 5
    assert spec.max_attempts == 3 and not spec.trust_env
    assert len(router.candidates("figure_structure")) == 1
    for task in ("visual_classify", "figure_self_check"):
        assert router.resolve(task).base_url == env["VLM_BASE_URL"]
        assert router.resolve(task).model_name == "qwen-vl"
