"""FakeLLM：按脚本返回预设响应的 ModelClient，并记录收到的全部请求。

与 OpenAICompatClient 保持同一契约：
- response_schema 不为空且响应未给出 parsed 时，对 content 做 Pydantic 校验；
- 校验失败时消耗脚本中的下一项作为"修复重试"，仍失败则抛 SchemaViolation。
"""

from __future__ import annotations

import itertools
from collections.abc import Callable
from typing import Any

from pydantic import BaseModel

from drugpaper.gateway.base import ChatRequest, ChatResponse, Message, SchemaViolation, ToolCall, Usage
from drugpaper.gateway.openai_compat import parse_structured

Step = ChatResponse | Exception | Callable[[ChatRequest], "ChatResponse | Exception"]

_ids = itertools.count(1)


def _cid() -> str:
    return f"call_{next(_ids)}"


def call(name: str, arguments: dict[str, Any] | None = None, *, id: str | None = None) -> ToolCall:  # noqa: A002
    return ToolCall(id=id or _cid(), name=name, arguments=arguments or {})


def tools(*calls: ToolCall, content: str | None = None) -> ChatResponse:
    return ChatResponse(
        content=content, tool_calls=list(calls), usage=Usage(input_tokens=10, output_tokens=5)
    )


def submit(
    answer: str, citations: list[dict[str, Any]] | None = None, warnings: list[str] | None = None
) -> ChatResponse:
    args: dict[str, Any] = {"answer": answer, "citations": citations or []}
    if warnings is not None:
        args["warnings"] = warnings
    return tools(call("submit_answer", args))


def text(content: str) -> ChatResponse:
    return ChatResponse(content=content, usage=Usage(input_tokens=10, output_tokens=5))


class FakeLLM:
    def __init__(self, script: list[Step] | Callable[[ChatRequest], ChatResponse | Exception]) -> None:
        self._script = script
        self.requests: list[ChatRequest] = []
        self.tasks: list[str] = []

    def _next(self, req: ChatRequest) -> ChatResponse:
        self.requests.append(req)
        if callable(self._script):
            item: Step = self._script
        else:
            if not self._script:
                raise AssertionError(f"FakeLLM 脚本已用完（第 {len(self.requests)} 次请求）")
            item = self._script.pop(0)
        if callable(item) and not isinstance(item, ChatResponse | Exception):
            item = item(req)
        if isinstance(item, Exception):
            raise item
        return item.model_copy(deep=True)

    async def chat(self, req: ChatRequest, *, task: str = "default") -> ChatResponse:
        self.tasks.append(task)
        resp = self._next(req)
        schema: type[BaseModel] | None = req.response_schema
        if schema is not None and resp.parsed is None:
            try:
                resp.parsed = parse_structured(resp.content, schema)
            except SchemaViolation as first:
                repair = req.model_copy(
                    update={
                        "messages": [
                            *req.messages,
                            Message(role="assistant", content=first.raw),
                            Message(
                                role="user", content="上一次输出不符合 Schema：" + "; ".join(first.errors)
                            ),
                        ]
                    }
                )
                resp = self._next(repair)
                resp.parsed = parse_structured(resp.content, schema)
        return resp

    def tool_names(self, i: int) -> list[str]:
        """第 i 次请求中提供给模型的工具名。"""
        return [t.name for t in self.requests[i].tools]


class ListTracer:
    """把事件收集到内存列表中，供断言使用。"""

    def __init__(self) -> None:
        self.events: list[tuple[str, str, dict[str, Any]]] = []

    def event(self, run_id: str, kind: str, payload: dict[str, Any]) -> None:
        self.events.append((run_id, kind, payload))

    def kinds(self) -> list[str]:
        return [k for _, k, _ in self.events]

    def of(self, kind: str) -> list[dict[str, Any]]:
        return [p for _, k, p in self.events if k == kind]
