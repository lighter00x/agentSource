"""Agent 状态、预算与答案模型。

LangGraph 状态中只存 JSON 友好的数据（消息以 Message.model_dump() 形式存放），
便于 checkpointer 序列化（P1 内存，P2 换 Postgres）。读取时用 state_messages() 还原。
"""

from __future__ import annotations

import operator
import time
from typing import Annotated, Any, TypedDict

from pydantic import BaseModel, Field

from drugpaper.gateway.base import Message, Usage

STOP_ANSWERED = "answered"
STOP_BUDGET_EXHAUSTED = "budget_exhausted"
STOP_ERROR = "error"
STOP_VALIDATION_FAILED = "validation_failed"


class Budget(BaseModel):
    max_steps: int = 8  # 一次"模型调用 + 工具执行"算一步
    max_model_calls: int = 10
    max_tool_calls: int = 20
    max_wall_time_s: float = 180

    def exhausted_reason(self, state: AgentState, now: float | None = None) -> str | None:
        """任一预算用尽时返回原因，否则返回 None。"""
        if state["steps"] >= self.max_steps:
            return f"steps {state['steps']}/{self.max_steps}"
        if state["model_calls"] >= self.max_model_calls:
            return f"model_calls {state['model_calls']}/{self.max_model_calls}"
        if state["tool_calls"] >= self.max_tool_calls:
            return f"tool_calls {state['tool_calls']}/{self.max_tool_calls}"
        elapsed = (now if now is not None else time.time()) - state["started_at"]
        if elapsed >= self.max_wall_time_s:
            return f"wall_time {elapsed:.1f}s/{self.max_wall_time_s}s"
        return None


class Citation(BaseModel):
    ref: str = Field(description="块 ID，必须来自工具返回结果，例如 p000.b007")
    quote: str | None = Field(default=None, description="可选：该块中的原文片段（逐字摘录）")


class AgentAnswer(BaseModel):
    answer: str = Field(description="回答正文")
    citations: list[Citation] = Field(default_factory=list, description="支撑回答的引用")
    warnings: list[str] = Field(default_factory=list, description="不确定之处、未找到依据等说明")


class AgentState(TypedDict):
    capability_state: dict[str, Any]
    run_id: str
    user_input: str
    ctx: dict[str, Any]  # ToolContext.model_dump()
    messages: Annotated[list[dict[str, Any]], operator.add]  # 历史消息（不含系统提示与用户问题）
    prompt: list[dict[str, Any]]  # assemble 产出：本轮发给模型的完整消息
    seen_refs: Annotated[list[str], operator.add]  # 工具结果中出现过的节点 ID；读取时转集合
    steps: int
    model_calls: int
    tool_calls: int
    started_at: float
    usage: dict[str, int]
    pending_calls: list[dict[str, Any]]  # 本轮待执行的普通工具调用（ToolCall.model_dump()）
    submission: dict[str, Any] | None  # 待校验的答案：source / arguments / parsed / call_ids
    route: str | None  # 节点给出的下一步，由条件边读取
    validation_retries: int
    budget_exhausted: bool
    final: dict[str, Any] | None
    stop_reason: str | None  # answered / budget_exhausted / error / validation_failed
    error: str | None
    warnings: list[str]


def initial_state(run_id: str, user_input: str, ctx: dict[str, Any]) -> AgentState:
    return AgentState(
        capability_state={},
        run_id=run_id,
        user_input=user_input,
        ctx=ctx,
        messages=[],
        prompt=[],
        seen_refs=[],
        steps=0,
        model_calls=0,
        tool_calls=0,
        started_at=time.time(),
        usage=Usage().model_dump(),
        pending_calls=[],
        submission=None,
        route=None,
        validation_retries=0,
        budget_exhausted=False,
        final=None,
        stop_reason=None,
        error=None,
        warnings=[],
    )


def state_messages(state: AgentState) -> list[Message]:
    return [Message.model_validate(m) for m in state.get("messages", [])]


def seen_ref_set(state: AgentState) -> set[str]:
    return set(state.get("seen_refs", []))


class AgentRunResult(BaseModel):
    run_id: str
    answer: dict[str, Any]  # answer_model.model_dump()；默认结构见 AgentAnswer
    stop_reason: str
    steps: int
    model_calls: int
    tool_calls: int
    usage: Usage
    seen_refs: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    error: str | None = None
    elapsed_ms: int = 0
    # P2 起：引用解析为证据（EvidenceRef 的 dict 形式：node_id、page、regions、char_span…），由装配层填充
    evidence: list[dict[str, Any]] = Field(default_factory=list)

    def answer_as[T: BaseModel](self, model: type[T]) -> T:
        return model.model_validate(self.answer)
