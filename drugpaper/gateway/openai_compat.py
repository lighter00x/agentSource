"""OpenAI 兼容客户端（sglang / vLLM / 多数云厂商兼容接口）。P1 唯一的 ModelClient 实现。

职责：
- 内部消息/工具/结构化请求 → OpenAI Chat Completions 或 Responses 请求；
- 响应 → ChatResponse（工具参数解析、结构化解析、token 统计）；
- 网络层重试（超时、429、5xx；指数退避 + 抖动）；
- 结构化输出降级（json_schema 不被支持时 → json_object + Schema 说明，按模型名缓存）；
- SchemaViolation 的一次修复重试。
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import mimetypes
import re
import time
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any, ClassVar, cast

import httpx2
import openai
import orjson
from openai import AsyncOpenAI
from openai.types.chat import ChatCompletion
from pydantic import BaseModel, ValidationError
from tenacity import AsyncRetrying, retry_if_exception, stop_after_attempt, wait_exponential, wait_random

from drugpaper.config.models_file import ResolvedModel
from drugpaper.gateway.base import (
    ChatRequest,
    ChatResponse,
    ImagePart,
    Message,
    ModelBadResponse,
    ModelError,
    ModelTimeout,
    ModelUnavailable,
    RateLimited,
    SchemaViolation,
    TextPart,
    ToolCall,
    Usage,
)

_THINK_RE = re.compile(r"^\s*<think>.*?</think>\s*", re.DOTALL)
_FENCE_RE = re.compile(r"^\s*```(?:json)?\s*(.*?)\s*```\s*$", re.DOTALL)

STRUCTURED_JSON_SCHEMA = "json_schema"
STRUCTURED_JSON_OBJECT = "json_object"
STRUCTURED_PROMPT_ONLY = "prompt_only"


def debug_request_body(kwargs: dict[str, Any]) -> dict[str, Any]:
    """Expose JSON payload separately from SDK options; never include auth headers."""
    body = {
        k: v for k, v in kwargs.items() if k not in {"extra_body", "timeout", "extra_headers", "extra_query"}
    }
    return {**body, **(kwargs.get("extra_body") or {})}


def normalize_base_url(base_url: str) -> str:
    """base_url 已含 /v1 时不再拼接。"""
    u = base_url.rstrip("/")
    return u if u.endswith("/v1") else u + "/v1"


def _sniff_mime(data: bytes, fallback: str) -> str:
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return fallback


def _image_data_url(part: ImagePart) -> str:
    if part.data_b64:
        head = part.data_b64[:64]
        try:
            raw = base64.b64decode(head + "=" * (-len(head) % 4))
        except (binascii.Error, ValueError):
            raw = b""
        return f"data:{_sniff_mime(raw, part.mime)};base64,{part.data_b64}"
    if part.blob_ref:
        # P1：blob_ref 视为本地路径；P2 起改为 BlobStore 读取
        p = Path(part.blob_ref)
        data = p.read_bytes()
        guessed = mimetypes.guess_type(p.name)[0] or part.mime
        mime = _sniff_mime(data, guessed)
        return f"data:{mime};base64,{base64.b64encode(data).decode('ascii')}"
    raise ValueError("ImagePart 需要 data_b64 或 blob_ref")


def _message_to_openai(m: Message) -> dict[str, Any]:
    d: dict[str, Any] = {"role": m.role}
    if m.role == "tool":
        d["tool_call_id"] = m.tool_call_id
        d["content"] = m.text()
        return d
    if isinstance(m.content, list):
        parts: list[dict[str, Any]] = []
        for p in m.content:
            if isinstance(p, TextPart):
                parts.append({"type": "text", "text": p.text})
            else:
                parts.append({"type": "image_url", "image_url": {"url": _image_data_url(p)}})
        d["content"] = parts
    else:
        d["content"] = m.content
    if m.tool_calls:
        d["tool_calls"] = [
            {
                "id": tc.id,
                "type": "function",
                "function": {
                    "name": tc.name,
                    "arguments": (
                        tc.raw_arguments
                        if tc.arguments_error and tc.raw_arguments is not None
                        else orjson.dumps(tc.arguments).decode()
                    ),
                },
            }
            for tc in m.tool_calls
        ]
    if m.name and m.role == "user":
        d["name"] = m.name
    return d


def _schema_instruction(schema_model: type[BaseModel]) -> str:
    schema = json.dumps(schema_model.model_json_schema(), ensure_ascii=False)
    return f"\n\n请只输出一个 JSON 对象，不要输出其他内容。JSON 必须符合以下 JSON Schema：\n{schema}"


def _with_schema_instruction(messages: list[Message], schema_model: type[BaseModel]) -> list[Message]:
    """在系统消息末尾追加 Schema 说明（json_object / prompt_only 模式）。"""
    out = list(messages)
    instr = _schema_instruction(schema_model)
    for i, m in enumerate(out):
        if m.role == "system":
            out[i] = m.model_copy(update={"content": m.text() + instr})
            return out
    return [Message(role="system", content=instr.strip()), *out]


def _clean_json_text(text: str) -> str:
    t = _THINK_RE.sub("", text)
    m = _FENCE_RE.match(t)
    if m:
        t = m.group(1)
    t = t.strip()
    if not t.startswith("{"):
        start, end = t.find("{"), t.rfind("}")
        if start != -1 and end > start:
            t = t[start : end + 1]
    return t


def parse_structured(content: str | None, schema_model: type[BaseModel]) -> BaseModel:
    raw = content or ""
    try:
        return schema_model.model_validate_json(_clean_json_text(raw))
    except ValidationError as e:
        errors = [f"{'.'.join(str(x) for x in err['loc']) or '<root>'}: {err['msg']}" for err in e.errors()]
        raise SchemaViolation(raw, errors) from e


def _parse_tool_call(tc_id: str, name: str, arguments: str | None) -> ToolCall:
    text = arguments or ""
    if not text.strip():
        return ToolCall(id=tc_id, name=name, arguments={})
    try:
        parsed = orjson.loads(text)
    except orjson.JSONDecodeError as e:
        return ToolCall(id=tc_id, name=name, raw_arguments=text, arguments_error=f"参数不是合法 JSON：{e}")
    if not isinstance(parsed, dict):
        return ToolCall(id=tc_id, name=name, raw_arguments=text, arguments_error="参数必须是 JSON 对象")
    return ToolCall(id=tc_id, name=name, arguments=parsed)


def _map_error(e: Exception) -> ModelError:
    if isinstance(e, ModelError):
        return e
    if isinstance(e, openai.APITimeoutError | httpx2.TimeoutException):
        return ModelTimeout(f"模型请求超时：{e}")
    if isinstance(e, openai.RateLimitError):
        return RateLimited(f"模型限流：{e}", status_code=429)
    if isinstance(e, openai.APIStatusError):
        if e.status_code >= 500:
            return ModelUnavailable(f"模型服务错误 {e.status_code}：{e}", status_code=e.status_code)
        return ModelBadResponse(f"模型请求被拒绝 {e.status_code}：{e}", status_code=e.status_code)
    if isinstance(e, openai.APIConnectionError | httpx2.TransportError):
        return ModelUnavailable(f"无法连接模型服务：{e}")
    if isinstance(e, openai.APIError):
        # Some compatible Responses gateways send an SSE error inside HTTP 200.
        # The SDK raises APIError rather than APIStatusError, so preserve retryability
        # for explicitly transient failures without retrying arbitrary bad responses.
        body = e.body if isinstance(e.body, dict) else {}
        detail = body.get("error", body)
        detail = detail if isinstance(detail, dict) else {}
        code = str(detail.get("code") or detail.get("type") or "").lower()
        if code in {"rate_limit_exceeded", "rate_limit_error"}:
            return RateLimited(f"模型流限流：{e}", status_code=429)
        if code in {
            "server_error",
            "internal_server_error",
            "service_unavailable",
            "upstream_unavailable",
        } or str(e).strip().lower() in {
            "upstream service is unavailable.",
            "upstream service is unavailable",
            "service unavailable",
        }:
            return ModelUnavailable(f"模型上游暂不可用：{e}")
    return ModelBadResponse(f"模型调用异常：{type(e).__name__}: {e}")


def _is_retryable(e: BaseException) -> bool:
    return isinstance(e, ModelError) and e.retryable


def _is_response_format_unsupported(e: ModelError) -> bool:
    return (
        isinstance(e, ModelBadResponse)
        and e.status_code == 400
        and ("response_format" in str(e) or "json_schema" in str(e))
    )


class OpenAICompatClient:
    """OpenAI 兼容的 ModelClient。一个实例对应一个模型端点，进程内复用连接池。"""

    # 结构化输出降级结果，按模型名缓存（进程级）
    _structured_mode_cache: ClassVar[dict[str, str]] = {}

    def __init__(
        self,
        model: ResolvedModel,
        *,
        max_attempts: int | None = None,
        retry_base_delay_s: float | None = None,
        retry_jitter_s: float | None = None,
        http_client: httpx2.AsyncClient | None = None,
        event_sink: Callable[[str, Any], Awaitable[None]] | None = None,
    ) -> None:
        self.model = model
        self._max_attempts = model.max_attempts if max_attempts is None else max_attempts
        self._base_delay = model.retry_base_delay_s if retry_base_delay_s is None else retry_base_delay_s
        self._jitter = model.retry_jitter_s if retry_jitter_s is None else retry_jitter_s
        self._event_sink = event_sink
        self._client = AsyncOpenAI(
            base_url=normalize_base_url(model.base_url),
            api_key=model.api_key,
            timeout=model.timeout_s,
            max_retries=0,  # 重试由本模块控制
            default_headers=model.extra_headers or None,
            http_client=http_client or httpx2.AsyncClient(trust_env=model.trust_env),
        )

    @classmethod
    def reset_structured_cache(cls) -> None:
        cls._structured_mode_cache.clear()

    # ------------------------------------------------------------ 公共接口

    async def chat(self, req: ChatRequest, *, task: str = "default") -> ChatResponse:
        # OpenAI 工具名只允许字母、数字、下划线和连字符；领域名可包含点号。
        names = {t.name for t in req.tools or []}
        names.update(c.name for m in req.messages for c in m.tool_calls or [])
        aliases = {
            name: "dp_wire_" + hashlib.sha256(name.encode()).hexdigest()[:32]
            for name in names
            if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", name) or name.startswith("dp_wire_")
        }
        if aliases:
            req = req.model_copy(
                update={
                    "tools": [t.model_copy(update={"name": aliases.get(t.name, t.name)}) for t in req.tools],
                    "messages": [
                        m.model_copy(
                            update={
                                "tool_calls": [
                                    c.model_copy(update={"name": aliases.get(c.name, c.name)})
                                    for c in m.tool_calls
                                ]
                            }
                        )
                        if m.tool_calls
                        else m
                        for m in req.messages
                    ],
                }
            )
        started = time.perf_counter()
        resp = await self._chat_once(req)
        if req.response_schema is not None:
            try:
                resp.parsed = parse_structured(resp.content, req.response_schema)
            except SchemaViolation as first:
                # 一次修复重试：把校验错误作为新的用户消息附上
                repair = req.model_copy(
                    update={
                        "messages": [
                            *req.messages,
                            Message(role="assistant", content=first.raw),
                            Message(
                                role="user",
                                content="上一次输出不符合 Schema：\n- "
                                + "\n- ".join(first.errors[:10])
                                + "\n请只输出符合 Schema 的 JSON。",
                            ),
                        ]
                    }
                )
                second = await self._chat_once(repair)
                second.usage = resp.usage + second.usage
                second.parsed = parse_structured(second.content, req.response_schema)  # 仍失败则抛出
                resp = second
        reverse = {wire: name for name, wire in aliases.items()}
        for call in resp.tool_calls:
            call.name = reverse.get(call.name, call.name)
        resp.latency_ms = int((time.perf_counter() - started) * 1000)
        return resp

    async def aclose(self) -> None:
        await self._client.close()

    # ------------------------------------------------------------ 内部实现

    def structured_mode(self) -> str:
        return self._structured_mode_cache.get(self.model.model_name, self.model.structured_output)

    def build_kwargs(self, req: ChatRequest, mode: str | None) -> dict[str, Any]:
        """ChatRequest → chat.completions.create 的参数（单测可直接断言）。"""
        messages = req.messages
        kwargs: dict[str, Any] = {
            "model": self.model.model_name,
            "temperature": req.temperature,
            "max_tokens": req.max_tokens,
            "timeout": req.timeout_s,
        }
        if req.tools:
            kwargs["tools"] = [
                {
                    "type": "function",
                    "function": {"name": t.name, "description": t.description, "parameters": t.parameters},
                }
                for t in req.tools
            ]
        if req.response_schema is not None:
            if mode == STRUCTURED_JSON_SCHEMA or mode == "guided_json":
                kwargs["response_format"] = {
                    "type": "json_schema",
                    "json_schema": {
                        "name": req.response_schema.__name__,
                        "schema": req.response_schema.model_json_schema(),
                        "strict": False,
                    },
                }
            elif mode == STRUCTURED_JSON_OBJECT:
                kwargs["response_format"] = {"type": "json_object"}
                messages = _with_schema_instruction(messages, req.response_schema)
            else:  # prompt_only
                messages = _with_schema_instruction(messages, req.response_schema)
        kwargs["messages"] = [_message_to_openai(m) for m in messages]

        extra_body: dict[str, Any] = dict(self.model.extra_body)
        if not req.thinking and self.model.thinking_toggle == "chat_template_kwargs":
            ctk = dict(extra_body.get("chat_template_kwargs") or {})
            ctk["enable_thinking"] = False
            extra_body["chat_template_kwargs"] = ctk
        if extra_body:
            kwargs["extra_body"] = extra_body
        return kwargs

    async def _chat_once(self, req: ChatRequest) -> ChatResponse:
        if self.model.api == "responses":
            return await self._responses_once(req)
        mode = self.structured_mode() if req.response_schema is not None else None
        try:
            completion = await self._request_with_retry(self.build_kwargs(req, mode))
        except ModelError as e:
            if mode == STRUCTURED_JSON_SCHEMA and _is_response_format_unsupported(e):
                self._structured_mode_cache[self.model.model_name] = STRUCTURED_JSON_OBJECT
                completion = await self._request_with_retry(self.build_kwargs(req, STRUCTURED_JSON_OBJECT))
            else:
                raise
        resp = self._to_response(completion)
        resp.usage.image_count = sum(
            1
            for m in req.messages
            if isinstance(m.content, list)
            for p in m.content
            if isinstance(p, ImagePart)
        )
        return resp

    def build_responses_kwargs(self, req: ChatRequest, mode: str | None) -> dict[str, Any]:
        """Responses 使用显式历史，保留 reasoning 项以支持工具调用及 checkpoint 恢复。"""
        messages = req.messages
        kwargs: dict[str, Any] = {
            "model": self.model.model_name,
            "max_output_tokens": req.max_tokens,
            "timeout": req.timeout_s,
            "store": False,
            "stream": True,
            "include": ["reasoning.encrypted_content"],
        }
        if req.tools:
            kwargs["tools"] = [
                {
                    "type": "function",
                    "name": t.name,
                    "description": t.description,
                    "parameters": t.parameters,
                    "strict": False,
                }
                for t in req.tools
            ]
        if req.response_schema is not None:
            if mode == STRUCTURED_JSON_SCHEMA:
                kwargs["text"] = {
                    "format": {
                        "type": "json_schema",
                        "name": req.response_schema.__name__,
                        "schema": req.response_schema.model_json_schema(),
                        "strict": False,
                    }
                }
            else:
                messages = _with_schema_instruction(messages, req.response_schema)
                if mode == STRUCTURED_JSON_OBJECT:
                    kwargs["text"] = {"format": {"type": "json_object"}}
        items: list[dict[str, Any]] = []
        for message in messages:
            if message.role == "tool":
                items.append(
                    {
                        "type": "function_call_output",
                        "call_id": message.tool_call_id,
                        "output": message.text(),
                    }
                )
            elif message.role == "assistant" and message.response_items:
                items.extend(message.response_items)
            else:
                if isinstance(message.content, list):
                    content = [
                        {"type": "input_text", "text": part.text}
                        if isinstance(part, TextPart)
                        else {"type": "input_image", "image_url": _image_data_url(part)}
                        for part in message.content
                    ]
                    items.append({"role": message.role, "content": content})
                elif message.content:
                    items.append({"role": message.role, "content": message.content})
                for call in message.tool_calls:
                    items.append(
                        {
                            "type": "function_call",
                            "call_id": call.id,
                            "name": call.name,
                            "arguments": call.raw_arguments or json.dumps(call.arguments),
                        }
                    )
        kwargs["input"] = items
        if self.model.extra_body:
            kwargs["extra_body"] = dict(self.model.extra_body)
        return kwargs

    async def _responses_once(self, req: ChatRequest) -> ChatResponse:
        mode = self.structured_mode() if req.response_schema is not None else None
        try:
            response = await self._request_responses(self.build_responses_kwargs(req, mode))
        except ModelError as exc:
            if mode == STRUCTURED_JSON_SCHEMA and _is_response_format_unsupported(exc):
                self._structured_mode_cache[self.model.model_name] = STRUCTURED_JSON_OBJECT
                response = await self._request_responses(
                    self.build_responses_kwargs(req, STRUCTURED_JSON_OBJECT)
                )
            else:
                raise
        response.usage.image_count = sum(
            isinstance(part, ImagePart)
            for message in req.messages
            if isinstance(message.content, list)
            for part in message.content
        )
        return response

    async def _request_responses(self, kwargs: dict[str, Any]) -> ChatResponse:
        retrying = AsyncRetrying(
            stop=stop_after_attempt(self._max_attempts),
            wait=wait_exponential(
                multiplier=self._base_delay, min=self._base_delay, max=self.model.retry_max_delay_s
            )
            + wait_random(0, self._jitter),
            before_sleep=self._record_retry,
            retry=retry_if_exception(_is_retryable),
            reraise=True,
        )
        async for attempt in retrying:
            with attempt:
                attempt_kwargs = self._attempt_kwargs(kwargs, attempt.retry_state.attempt_number)
                try:
                    if self._event_sink:
                        await self._event_sink(
                            "transport_request",
                            {
                                "api": "responses",
                                "attempt": attempt.retry_state.attempt_number,
                                "body": debug_request_body(attempt_kwargs),
                                "timeout": attempt_kwargs.get("timeout"),
                            },
                        )
                    stream = await self._client.responses.create(**attempt_kwargs)
                    completed = None
                    finalized: dict[int, dict[str, Any]] = {}
                    async with stream:
                        async for event in stream:
                            if event.type == "response.output_item.done":
                                finalized[event.output_index] = event.item.model_dump(
                                    mode="json", exclude_none=True
                                )
                            elif event.type == "response.completed":
                                completed = event.response
                            elif event.type in ("response.failed", "error"):
                                payload = event.model_dump(mode="json")
                                detail = (
                                    payload.get("response", {}).get("error")
                                    if event.type == "response.failed"
                                    else payload
                                )
                                detail = detail if isinstance(detail, dict) else {}
                                failure = openai.APIError(
                                    str(detail.get("message") or f"Responses 未完成：{event.type}"),
                                    request=httpx2.Request(
                                        "POST", normalize_base_url(self.model.base_url) + "/responses"
                                    ),
                                    body=detail,
                                )
                                raise _map_error(failure)
                            elif event.type == "response.incomplete":
                                raise ModelBadResponse(f"Responses 未完成：{event.type}")
                    if completed is None:
                        raise ModelBadResponse("Responses 流缺少 response.completed")
                    if self._event_sink:
                        await self._event_sink(
                            "transport_response",
                            {
                                "api": "responses",
                                "attempt": attempt.retry_state.attempt_number,
                                "body": completed.model_dump(mode="json"),
                                "finalized": finalized,
                            },
                        )
                    # 部分兼容网关的 completed.output 为空，完整输出在 output_item.done 中。
                    output = [x.model_dump(mode="json", exclude_none=True) for x in completed.output]
                    if not output:
                        output = [finalized[index] for index in sorted(finalized)]
                    text = "".join(
                        part.get("text", "")
                        for item in output
                        if item["type"] == "message"
                        for part in item.get("content", [])
                        if part["type"] == "output_text"
                    )
                    calls = [
                        _parse_tool_call(item["call_id"], item["name"], item["arguments"])
                        for item in output
                        if item["type"] == "function_call"
                    ]
                    if not text and not calls:
                        raise ModelBadResponse("Responses 没有返回文本或工具调用")
                    usage = completed.usage
                    return ChatResponse(
                        content=text or None,
                        tool_calls=calls,
                        response_items=output,
                        model=completed.model or self.model.model_name,
                        usage=Usage(
                            input_tokens=usage.input_tokens if usage else 0,
                            output_tokens=usage.output_tokens if usage else 0,
                        ),
                    )
                except ModelError as exc:
                    if self._event_sink:
                        await self._event_sink(
                            "transport_error",
                            {"attempt": attempt.retry_state.attempt_number, "error": str(exc)},
                        )
                    raise
                except Exception as exc:
                    if self._event_sink:
                        await self._event_sink(
                            "transport_error",
                            {"attempt": attempt.retry_state.attempt_number, "error": str(exc)},
                        )
                    raise _map_error(exc) from exc
        raise ModelUnavailable("Responses 重试次数耗尽")

    def _attempt_kwargs(self, kwargs: dict[str, Any], attempt: int) -> dict[str, Any]:
        result = dict(kwargs)
        base = result.get("timeout", self.model.timeout_s)
        if isinstance(base, (int, float)):
            timeout = base * self.model.retry_timeout_multiplier ** (attempt - 1)
            if self.model.retry_timeout_max_s is not None:
                timeout = min(timeout, max(base, self.model.retry_timeout_max_s))
            result["timeout"] = timeout
        return result

    async def _record_retry(self, state: Any) -> None:
        if self._event_sink:
            await self._event_sink(
                "transport_retry",
                {
                    "attempt": state.attempt_number,
                    "next_attempt": state.attempt_number + 1,
                    "delay_s": state.next_action.sleep,
                    "max_attempts": self._max_attempts,
                },
            )

    async def _request_with_retry(self, kwargs: dict[str, Any]) -> ChatCompletion:
        retrying = AsyncRetrying(
            stop=stop_after_attempt(self._max_attempts),
            wait=wait_exponential(
                multiplier=self._base_delay, min=self._base_delay, max=self.model.retry_max_delay_s
            )
            + wait_random(0, self._jitter),
            before_sleep=self._record_retry,
            retry=retry_if_exception(_is_retryable),
            reraise=True,
        )
        async for attempt in retrying:
            with attempt:
                attempt_kwargs = self._attempt_kwargs(kwargs, attempt.retry_state.attempt_number)
                try:
                    if self._event_sink:
                        await self._event_sink(
                            "transport_request",
                            {
                                "api": "chat_completions",
                                "attempt": attempt.retry_state.attempt_number,
                                "body": debug_request_body(attempt_kwargs),
                                "timeout": attempt_kwargs.get("timeout"),
                            },
                        )
                    result = await self._client.chat.completions.create(**attempt_kwargs)
                    if self._event_sink:
                        await self._event_sink(
                            "transport_response",
                            {
                                "api": "chat_completions",
                                "attempt": attempt.retry_state.attempt_number,
                                "body": result.model_dump(mode="json"),
                            },
                        )
                except Exception as e:  # noqa: BLE001 - 统一映射为类型化异常
                    if self._event_sink:
                        await self._event_sink(
                            "transport_error",
                            {"attempt": attempt.retry_state.attempt_number, "error": str(e)},
                        )
                    raise _map_error(e) from e
                return cast(ChatCompletion, result)
        raise ModelUnavailable("重试次数耗尽")  # pragma: no cover - reraise=True 时不可达

    def _to_response(self, completion: ChatCompletion) -> ChatResponse:
        if not completion.choices:
            raise ModelBadResponse("模型响应中没有 choices")
        msg = completion.choices[0].message
        content = msg.content
        if content is not None:
            content = _THINK_RE.sub("", content)
        tool_calls: list[ToolCall] = []
        for tc in msg.tool_calls or []:
            fn = getattr(tc, "function", None)
            if fn is None:
                continue
            tool_calls.append(_parse_tool_call(tc.id, fn.name, fn.arguments))
        usage = Usage()
        if completion.usage is not None:
            usage = Usage(
                input_tokens=completion.usage.prompt_tokens or 0,
                output_tokens=completion.usage.completion_tokens or 0,
            )
        return ChatResponse(
            content=content,
            tool_calls=tool_calls,
            usage=usage,
            model=completion.model or self.model.model_name,
        )
