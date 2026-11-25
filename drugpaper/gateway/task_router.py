"""按任务名调用模型（执行方案 S3.7）。

`RoutedModelClient` 实现 `ModelClient`：`chat(req, task="visual_classify")` 时
1. 按 `config/models.yaml` 解析 task → stage → pool → model → provider（P5 已加端点共享限流，支持显式启用的重试耗尽回退，完整网关仍属P7）；
2. 合并任务参数：请求中**没有显式设置**的 temperature / max_tokens / timeout_s / thinking 取任务配置；
3. 图片按任务的 `image_max_side` 等比缩小；
4. 每个模型一个底层客户端（复用连接池）。

业务代码与 Skill 只写任务名，不写模型名或地址。
"""

from __future__ import annotations

import base64
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from drugpaper.config.models_file import (
    ModelsConfigError,
    ModelsFile,
    ResolvedModel,
    default_env,
    load_models_file,
    resolve_task,
)
from drugpaper.gateway.base import (
    ChatRequest,
    ChatResponse,
    ContentPart,
    ImagePart,
    Message,
    ModelClient,
    ModelError,
)
from drugpaper.render.images import limit_max_side

ClientFactory = Callable[[ResolvedModel], ModelClient]


class TaskRouter:
    def __init__(self, models: ModelsFile, env: Mapping[str, str] | None = None) -> None:
        self.models = models
        self.env = dict(env) if env is not None else default_env()
        self._cache: dict[str, ResolvedModel] = {}

    @classmethod
    def from_file(cls, path: Path | str, env: Mapping[str, str] | None = None) -> TaskRouter:
        return cls(load_models_file(path), env)

    def tasks(self) -> set[str]:
        return set(self.models.tasks)

    def has_task(self, task: str) -> bool:
        return task in self.models.tasks

    def resolve(self, task: str) -> ResolvedModel:
        if task not in self._cache:
            self._cache[task] = resolve_task(self.models, task, self.env)
        return self._cache[task]

    def candidates(self, task: str) -> list[ResolvedModel]:
        primary = self.resolve(task)
        stage = self.models.stages[primary.stage]
        if not stage.fallback_on_retry_exhausted or stage.model or not stage.pool:
            return [primary]
        keys = list(dict.fromkeys(self.models.pools[stage.pool]))
        return [
            primary,
            *[
                resolve_task(self.models, task, self.env, candidate=key)
                for key in keys
                if key != primary.model_key
            ],
        ]

    def public_config(self, task: str) -> dict[str, Any]:
        """Cache identity includes every enabled model, never credentials."""
        return {"candidates": [spec.model_dump(exclude={"api_key"}) for spec in self.candidates(task)]}

    def stage_of(self, task: str) -> str:
        if task not in self.models.tasks:
            raise ModelsConfigError(f"任务 {task!r} 未定义")
        return self.models.tasks[task].stage


def _limit_images(messages: list[Message], max_side: int) -> list[Message]:
    out: list[Message] = []
    for m in messages:
        if not isinstance(m.content, list):
            out.append(m)
            continue
        parts: list[ContentPart] = []
        for p in m.content:
            if isinstance(p, ImagePart) and p.data_b64:
                data, mime = limit_max_side(base64.b64decode(p.data_b64), max_side)
                parts.append(p.model_copy(update={"data_b64": base64.b64encode(data).decode(), "mime": mime}))
            elif isinstance(p, ImagePart) and p.blob_ref:
                data, mime = limit_max_side(Path(p.blob_ref).read_bytes(), max_side)
                parts.append(ImagePart(data_b64=base64.b64encode(data).decode(), mime=mime))
            else:
                parts.append(p)
        out.append(m.model_copy(update={"content": parts}))
    return out


def apply_task_params(req: ChatRequest, spec: ResolvedModel) -> ChatRequest:
    explicit = req.model_fields_set
    update: dict[str, object] = {}
    if "temperature" not in explicit:
        update["temperature"] = spec.temperature
    if "max_tokens" not in explicit:
        update["max_tokens"] = spec.max_tokens
    if "timeout_s" not in explicit:
        update["timeout_s"] = spec.timeout_s
    if "thinking" not in explicit:
        update["thinking"] = spec.thinking
    if spec.image_max_side:
        update["messages"] = _limit_images(req.messages, spec.image_max_side)
    return req.model_copy(update=update) if update else req


class RoutedModelClient:
    def __init__(self, router: TaskRouter, client_factory: ClientFactory | None = None) -> None:
        self.router = router
        self._factory = client_factory or _default_factory
        self._clients: dict[str, ModelClient] = {}
        self.limiter: Any = None
        self.calls: list[tuple[str, str]] = []  # (task, model_key)，便于观测与测试

    def client_for(self, task: str) -> tuple[ModelClient, ResolvedModel]:
        spec = self.router.resolve(task)
        return self._client_for_spec(spec), spec

    def _client_for_spec(self, spec: ResolvedModel) -> ModelClient:
        key = f"{spec.provider_key}/{spec.model_key}"
        if key not in self._clients:
            self._clients[key] = self._factory(spec)
        return self._clients[key]

    async def chat(self, req: ChatRequest, *, task: str = "default") -> ChatResponse:
        candidates = self.router.candidates(task)
        for index, spec in enumerate(candidates):
            client = self._client_for_spec(spec)
            self.calls.append((task, spec.model_key))
            try:
                if self.limiter is not None:
                    async with self.limiter.slot(spec.base_url):
                        return await client.chat(apply_task_params(req, spec), task=task)
                return await client.chat(apply_task_params(req, spec), task=task)
            except ModelError as exc:
                # Each transport client exhausts its bounded retries first.
                # Authentication/schema/request errors require correction, not failover.
                if not exc.retryable or index + 1 == len(candidates):
                    raise
        raise AssertionError("empty model candidates")

    async def aclose(self) -> None:
        if self.limiter is not None:
            await self.limiter.aclose()
        for c in self._clients.values():
            close = getattr(c, "aclose", None)
            if close is not None:
                await close()


def _default_factory(spec: ResolvedModel) -> ModelClient:
    from drugpaper.gateway.openai_compat import OpenAICompatClient

    return OpenAICompatClient(spec)
