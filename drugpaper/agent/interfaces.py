"""AgentRuntime 的扩展点。后续阶段只替换实现，不改调用方。

| 接口 | P1 实现 | 替换阶段 |
|---|---|---|
| ToolsetProvider | StaticToolset | P3 DisclosureToolset（渐进式披露） |
| ContextAssembler | SimpleAssembler | P3 PromptAssembler |
| AnswerValidator | CitationValidator / NoopValidator | P5/P6 各层校验器 |
| Tracer | JsonlTracer / ConsolePrinter | P2 RunRecord 入库 |
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel

from drugpaper.agent.state import AgentState
from drugpaper.gateway.base import Message
from drugpaper.tools.protocol import Tool


class ToolsetProvider(Protocol):
    def tools_for(self, state: AgentState) -> list[Tool]:
        """本轮提供给模型的工具（不含运行时自带的 submit_answer）。"""
        ...


class ContextAssembler(Protocol):
    def build(self, state: AgentState) -> list[Message]:
        """组装本轮发给模型的完整消息列表。"""
        ...


class AnswerValidator(Protocol):
    def validate(self, answer: BaseModel, state: AgentState) -> list[str]:
        """返回问题列表；空列表表示通过。"""
        ...


@runtime_checkable
class AnswerSanitizer(Protocol):
    """可选能力：重答仍不通过时，由运行时调用它清理答案（例如删除无效引用）。"""

    def sanitize(self, answer: BaseModel, state: AgentState) -> BaseModel: ...


class Tracer(Protocol):
    def event(self, run_id: str, kind: str, payload: dict[str, Any]) -> None: ...
