"""AgentRuntime：基于 LangGraph 的 ReAct 循环（执行方案 S1.7）。

图结构：
    START → assemble → call_model ─┬─ tools    → execute_tools ─┬─ continue → assemble
                                   │                            ├─ answer   → validate
                                   │                            └─ finalize → force_finalize
                                   ├─ answer   → validate
                                   ├─ finalize → force_finalize → validate
                                   └─ error    → END
    validate ─┬─ done  → END
              └─ retry → assemble

结束方式：运行时给模型额外提供 submit_answer 工具（参数即 answer_model 的 Schema）。
模型直接输出纯文本时，再发起 1 次结构化请求把文本转成 answer_model（计入预算）。
"""

from __future__ import annotations

import asyncio
import time
from typing import Any, cast

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel, ValidationError

from drugpaper.agent.config import AgentConfig
from drugpaper.agent.interfaces import AnswerSanitizer, Tracer
from drugpaper.agent.state import (
    STOP_ANSWERED,
    STOP_BUDGET_EXHAUSTED,
    STOP_ERROR,
    STOP_VALIDATION_FAILED,
    AgentRunResult,
    AgentState,
    initial_state,
)
from drugpaper.gateway.base import (
    ChatRequest,
    ChatResponse,
    Message,
    ModelClient,
    ModelError,
    SchemaViolation,
    ToolCall,
    ToolSchema,
    Usage,
)
from drugpaper.tools.protocol import ToolContext, ToolResult, compact_json_schema
from drugpaper.tools.registry import ToolRegistry

SUBMIT_ANSWER = "submit_answer"

FINALIZE_PROMPT = "预算已用尽，请只基于已获取的信息，调用 submit_answer 作答，并在 warnings 中说明“预算用尽，答案可能不完整”。"
CONVERT_PROMPT = "请把你上面的回答整理为一个 JSON 对象，符合要求的 Schema。只输出 JSON，不要输出其他内容。"
FALLBACK_BUDGET_TEXT = "未能在预算内完成"
BUDGET_WARNING = "预算已用尽，答案仅基于已获取的信息，可能不完整"
SANITIZED_WARNING = "已删除无效引用，部分陈述未找到原文依据"

ARG_SUMMARY_CHARS = 200


def _summ(value: Any, limit: int = ARG_SUMMARY_CHARS) -> str:
    s = value if isinstance(value, str) else repr(value)
    return s if len(s) <= limit else s[:limit] + "…"


def _format_validation_error(e: ValidationError) -> list[str]:
    return [f"{'.'.join(str(x) for x in err['loc']) or '<root>'}: {err['msg']}" for err in e.errors()[:8]]


class AgentRuntime:
    def __init__(
        self,
        config: AgentConfig,
        model: ModelClient,
        registry: ToolRegistry,
        tracer: Tracer,
        checkpointer: BaseCheckpointSaver[Any] | None = None,
    ) -> None:
        self.config = config
        self.model = model
        self.registry = registry
        self.tracer = tracer
        self._assembler = config.get_assembler()
        self._submit_schema = ToolSchema(
            name=SUBMIT_ANSWER,
            description="提交最终答案。作答时必须调用此工具，参数必须符合 Schema；引用只能使用工具结果中出现过的块 ID。",
            parameters=compact_json_schema(config.answer_model.model_json_schema()),
        )
        self._graph = self._build_graph().compile(checkpointer=checkpointer or InMemorySaver())

    # ================================================================ 图

    def _build_graph(self) -> StateGraph[AgentState]:
        g: StateGraph[AgentState] = StateGraph(AgentState)
        g.add_node("assemble", self._assemble)
        g.add_node("call_model", self._call_model)
        g.add_node("execute_tools", self._execute_tools)
        g.add_node("force_finalize", self._force_finalize)
        g.add_node("validate", self._validate)
        g.add_edge(START, "assemble")
        g.add_edge("assemble", "call_model")
        g.add_conditional_edges(
            "call_model",
            self._route_after_model,
            {"tools": "execute_tools", "answer": "validate", "finalize": "force_finalize", "error": END},
        )
        g.add_conditional_edges(
            "execute_tools",
            self._route_after_tools,
            {"continue": "assemble", "finalize": "force_finalize", "answer": "validate"},
        )
        g.add_edge("force_finalize", "validate")
        g.add_conditional_edges("validate", self._route_after_validate, {"done": END, "retry": "assemble"})
        return g

    @staticmethod
    def _route_after_model(state: AgentState) -> str:
        return state["route"] or "error"

    @staticmethod
    def _route_after_tools(state: AgentState) -> str:
        return state["route"] or "continue"

    @staticmethod
    def _route_after_validate(state: AgentState) -> str:
        return state["route"] or "done"

    # ================================================================ 对外接口

    def _graph_config(self, run_id: str) -> RunnableConfig:
        budget = self.config.budget
        return {
            "configurable": {"thread_id": run_id},
            # 每步最多 3 个节点，再加 finalize / validate / 重答的余量
            "recursion_limit": budget.max_steps * 3 * (self.config.max_validation_retries + 1) + 20,
        }

    async def run(self, user_input: str, ctx: ToolContext) -> AgentRunResult:
        state = initial_state(ctx.run_id, user_input, ctx.model_dump())
        return await self._execute(state, ctx, user_input=user_input, resumed=False)

    async def resume(self, ctx: ToolContext) -> AgentRunResult:
        """从 checkpointer 中 `thread_id = ctx.run_id` 的最后一个检查点继续执行（进程中断后恢复）。

        已经结束的运行直接返回结果；耗时预算从恢复时刻重新计算（已用的步数、调用数照常计入）。
        """
        cfg = self._graph_config(ctx.run_id)
        snap = await self._graph.aget_state(cfg)
        values = snap.values
        if not isinstance(values, dict) or not values:
            raise KeyError(f"没有 run_id={ctx.run_id} 的检查点，无法恢复")
        state = cast(AgentState, values)
        if not snap.next:  # 已经结束
            return self._to_result(state, 0)
        await self._graph.aupdate_state(cfg, {"started_at": time.time()})
        return await self._execute(None, ctx, user_input=state.get("user_input", ""), resumed=True)

    async def _execute(
        self, state: AgentState | None, ctx: ToolContext, *, user_input: str, resumed: bool
    ) -> AgentRunResult:
        run_id = ctx.run_id
        budget = self.config.budget
        graph_config = self._graph_config(run_id)
        self.tracer.event(
            run_id,
            "run_start",
            {
                "agent": self.config.name,
                "trace_id": ctx.trace_id,
                "parent_run_id": ctx.parent_run_id,
                "document_id": ctx.document_id,
                "user_input": _summ(user_input),
                "budget": budget.model_dump(),
                "resumed": resumed,
            },
        )
        started = time.perf_counter()
        final_state: AgentState
        fallback = state if state is not None else initial_state(run_id, user_input, ctx.model_dump())
        try:
            async with asyncio.timeout(budget.max_wall_time_s + 30):
                final_state = cast(AgentState, await self._graph.ainvoke(state, graph_config))
        except TimeoutError:
            self.tracer.event(run_id, "cancelled", {"reason": "wall_time_exceeded"})
            final_state = await self._last_state(graph_config, fallback)
            final_state["stop_reason"] = STOP_ERROR
            final_state["error"] = f"运行超时（>{budget.max_wall_time_s + 30:.0f}s）"
            final_state["final"] = None
        except asyncio.CancelledError:
            self.tracer.event(run_id, "cancelled", {"reason": "cancelled"})
            raise

        result = self._to_result(final_state, int((time.perf_counter() - started) * 1000))
        self.tracer.event(
            run_id,
            "run_end",
            {
                "stop_reason": result.stop_reason,
                "steps": result.steps,
                "model_calls": result.model_calls,
                "tool_calls": result.tool_calls,
                "usage": result.usage.model_dump(),
                "elapsed_ms": result.elapsed_ms,
                "error": result.error,
                "warnings": result.warnings,
            },
        )
        return result

    async def _last_state(self, graph_config: RunnableConfig, fallback: AgentState) -> AgentState:
        try:
            snap = await self._graph.aget_state(graph_config)
            values = snap.values
            if isinstance(values, dict) and values:
                return cast(AgentState, values)
        except Exception:  # noqa: BLE001 - 只用于超时后的尽力恢复
            pass
        return fallback

    def _to_result(self, s: AgentState, elapsed_ms: int) -> AgentRunResult:
        final = s.get("final")
        if final is None:
            final = self._fallback_answer(s.get("error") or FALLBACK_BUDGET_TEXT, list(s.get("warnings", [])))
        return AgentRunResult(
            run_id=s["run_id"],
            answer=final,
            stop_reason=s.get("stop_reason") or STOP_ERROR,
            steps=s["steps"],
            model_calls=s["model_calls"],
            tool_calls=s["tool_calls"],
            usage=Usage.model_validate(s["usage"]),
            seen_refs=sorted(set(s.get("seen_refs", []))),
            warnings=list(s.get("warnings", [])),
            error=s.get("error"),
            elapsed_ms=elapsed_ms,
        )

    # ================================================================ 节点

    async def _assemble(self, state: AgentState) -> dict[str, Any]:
        prepare = getattr(self.config.toolset, "prepare", None)
        capabilities = prepare(state) if prepare else state.get("capability_state", {})
        current: AgentState = {**state, "capability_state": capabilities}
        prompt = [m.model_dump() for m in self._assembler.build(current)]
        assembled = getattr(self._assembler, "last", None)
        if assembled:
            capabilities["prompt_disclosed"] = [
                {"name": d.name, "level": d.level, "tokens": d.tokens} for d in assembled.disclosed
            ]
        return {
            "prompt": prompt,
            "route": None,
            "capability_state": capabilities,
        }

    async def _call_model(self, state: AgentState) -> dict[str, Any]:
        reason = self.config.budget.exhausted_reason(state)
        if reason is not None:
            self.tracer.event(
                state["run_id"], "budget_exhausted", {"reason": reason, "where": "before_model"}
            )
            return {"route": "finalize", "budget_exhausted": True}

        tools = self.config.toolset.tools_for(state)
        prompt = [Message.model_validate(m) for m in state["prompt"]]
        req = self._request(prompt, [t.spec.to_schema() for t in tools] + [self._submit_schema])
        updates: dict[str, Any] = {"steps": state["steps"] + 1}
        try:
            resp = await self._chat(state, req, updates, phase="agent")
        except ModelError as e:
            return {**updates, **self._error_update(state, f"模型调用失败：{type(e).__name__}: {e}")}

        record = getattr(self.config.toolset, "record", None)
        if record:
            observed: AgentState = {**state, "pending_calls": [c.model_dump() for c in resp.tool_calls]}
            await record(observed)

        assistant = Message(
            role="assistant",
            content=resp.content,
            tool_calls=resp.tool_calls,
            response_items=resp.response_items,
        )
        updates["messages"] = [assistant.model_dump()]
        submit_calls = [c for c in resp.tool_calls if c.name == SUBMIT_ANSWER]
        other_calls = [c for c in resp.tool_calls if c.name != SUBMIT_ANSWER]
        submission: dict[str, Any] | None = None
        if submit_calls:
            first = submit_calls[0]
            submission = {
                "source": "tool",
                "arguments": first.arguments,
                "arguments_error": first.arguments_error,
                "call_ids": [c.id for c in submit_calls],
            }

        if other_calls:
            return {
                **updates,
                "pending_calls": [c.model_dump() for c in other_calls],
                "submission": submission,
                "route": "tools",
            }
        if submission is not None:
            return {**updates, "submission": submission, "route": "answer"}

        # 纯文本：再发起 1 次结构化请求，把文本转为 answer_model
        text = (resp.content or "").strip()
        if not text:
            return {**updates, "submission": {"source": "empty"}, "route": "answer"}
        convert_req = self._request(
            [*prompt, assistant, Message(role="user", content=CONVERT_PROMPT)],
            [],
            response_schema=self.config.answer_model,
        )
        try:
            converted = await self._chat(state, convert_req, updates, phase="convert")
        except SchemaViolation as e:
            err = f"结构化转换失败（已重试 1 次）：{'; '.join(e.errors[:5])}"
            return {**updates, **self._error_update(state, err, draft=text)}
        except ModelError as e:
            return {
                **updates,
                **self._error_update(state, f"结构化转换调用失败：{type(e).__name__}: {e}", draft=text),
            }
        parsed = converted.parsed
        if parsed is None:
            return {**updates, **self._error_update(state, "结构化转换没有返回解析结果", draft=text)}
        return {**updates, "submission": {"source": "text", "parsed": parsed.model_dump()}, "route": "answer"}

    async def _execute_tools(self, state: AgentState) -> dict[str, Any]:
        calls = [ToolCall.model_validate(c) for c in state["pending_calls"]]
        ctx = ToolContext.model_validate(state["ctx"])
        import copy

        ctx.capability_state = copy.deepcopy(state.get("capability_state", {}))
        allowed = {t.spec.name for t in self.config.toolset.tools_for(state)}
        remaining = max(0, self.config.budget.max_tool_calls - state["tool_calls"])
        to_run, skipped = calls[:remaining], calls[remaining:]

        results: list[ToolResult] = list(
            await asyncio.gather(*(self._invoke_tool(state["run_id"], ctx, c, allowed) for c in to_run))
        )
        results += [ToolResult(ok=False, error="budget exhausted: 工具调用次数已达上限") for _ in skipped]
        loaded = ctx.capability_state.get("loaded", {})
        for c, result in zip(to_run, results, strict=False):
            if result.ok:
                for name in loaded:
                    index = getattr(self.config.toolset, "index", None)
                    capability = index.entries.get(name) if index else None
                    if capability and c.name in capability.tools:
                        loaded[name] = state["steps"]

        messages = [
            Message(role="tool", tool_call_id=c.id, name=c.name, content=r.to_message_content()).model_dump()
            for c, r in zip(calls, results, strict=True)
        ]
        refs = [ref for r in results if r.ok for ref in r.refs]
        new_state: AgentState = {**state, "tool_calls": state["tool_calls"] + len(to_run)}

        route = "continue"
        if state["submission"] is not None:
            route = "answer"  # 同一轮既有 submit_answer 又有其他工具：先执行工具，再校验答案
        else:
            reason = f"tool_calls: {len(skipped)} 个调用因超出上限未执行" if skipped else None
            reason = reason or self.config.budget.exhausted_reason(new_state)
            if reason is not None:
                self.tracer.event(
                    state["run_id"], "budget_exhausted", {"reason": reason, "where": "after_tools"}
                )
                route = "finalize"
        return {
            "capability_state": ctx.capability_state,
            "messages": messages,
            "seen_refs": refs,
            "tool_calls": new_state["tool_calls"],
            "pending_calls": [],
            "route": route,
            "budget_exhausted": route == "finalize" or state["budget_exhausted"],
        }

    async def _force_finalize(self, state: AgentState) -> dict[str, Any]:
        prompt = [*self._assembler.build(state), Message(role="user", content=FINALIZE_PROMPT)]
        req = self._request(prompt, [self._submit_schema])
        updates: dict[str, Any] = {
            "budget_exhausted": True,
            "messages": [Message(role="user", content=FINALIZE_PROMPT).model_dump()],
        }
        warnings = [*state["warnings"], BUDGET_WARNING]
        try:
            resp = await self._chat(
                state, req, updates, phase="force_finalize"
            )  # 不受 max_model_calls 限制，但计数
        except ModelError as e:
            warnings.append(f"强制作答调用失败：{type(e).__name__}: {e}")
            resp = None

        submission: dict[str, Any]
        submit = next((c for c in resp.tool_calls if c.name == SUBMIT_ANSWER), None) if resp else None
        if resp is not None:
            assistant = Message(role="assistant", content=resp.content, tool_calls=resp.tool_calls)
            updates["messages"] = [*updates["messages"], assistant.model_dump()]
        if submit is not None:
            submission = {
                "source": "forced",
                "arguments": submit.arguments,
                "arguments_error": submit.arguments_error,
                "call_ids": [submit.id],
            }
        else:
            submission = {
                "source": "fallback",
                "parsed": self._fallback_answer(FALLBACK_BUDGET_TEXT, warnings),
            }
        return {
            **updates,
            "submission": submission,
            "warnings": warnings,
            "stop_reason": STOP_BUDGET_EXHAUSTED,
        }

    async def _validate(self, state: AgentState) -> dict[str, Any]:
        sub = state["submission"] or {"source": "empty"}
        source = sub.get("source")
        warnings = list(state["warnings"])
        exhausted = state["budget_exhausted"]
        base_stop = STOP_BUDGET_EXHAUSTED if exhausted else None

        if source == "fallback":
            self.tracer.event(state["run_id"], "validation", {"source": source, "ok": False, "issues": []})
            return {
                "final": sub["parsed"],
                "stop_reason": STOP_BUDGET_EXHAUSTED,
                "route": "done",
                "warnings": warnings,
            }

        answer: BaseModel | None = None
        issues: list[str] = []
        if source == "empty":
            issues = ["模型没有给出答案：请调用 submit_answer 提交答案"]
        elif sub.get("arguments_error"):
            issues = [f"submit_answer 的参数不是合法 JSON：{sub['arguments_error']}"]
        else:
            try:
                raw = sub["parsed"] if "parsed" in sub else sub.get("arguments", {})
                answer = self.config.answer_model.model_validate(raw)
            except ValidationError as e:
                issues = ["submit_answer 参数不符合 Schema：" + "; ".join(_format_validation_error(e))]
        if answer is not None:
            for v in self.config.validators:
                issues.extend(v.validate(answer, state))

        retries = state["validation_retries"]
        can_retry = bool(issues) and retries < self.config.max_validation_retries and not exhausted
        self.tracer.event(
            state["run_id"],
            "validation",
            {
                "source": source,
                "ok": not issues,
                "issues": issues,
                "retry": can_retry,
                "attempt": retries + 1,
            },
        )

        if can_retry:
            feedback: list[dict[str, Any]] = [
                Message(
                    role="tool",
                    tool_call_id=cid,
                    name=SUBMIT_ANSWER,
                    content='{"ok":false,"error":"答案未通过校验"}',
                ).model_dump()
                for cid in sub.get("call_ids", [])
            ]
            feedback.append(
                Message(
                    role="user",
                    content="答案未通过校验：\n- "
                    + "\n- ".join(issues)
                    + "\n请修正后重新调用 submit_answer 提交。",
                ).model_dump()
            )
            return {
                "messages": feedback,
                "validation_retries": retries + 1,
                "submission": None,
                "route": "retry",
            }

        if answer is None:
            warnings.extend(issues)
            final = self._fallback_answer("未能给出符合格式的答案", warnings)
            return {
                "final": final,
                "stop_reason": base_stop or STOP_VALIDATION_FAILED,
                "warnings": warnings,
                "route": "done",
            }

        if issues:
            for v in self.config.validators:
                if isinstance(v, AnswerSanitizer):
                    cleaned = v.sanitize(answer, state)
                    if cleaned is not answer and SANITIZED_WARNING not in warnings:
                        warnings.append(SANITIZED_WARNING)
                    answer = cleaned
            warnings.extend(issues)
        stop = base_stop or (STOP_VALIDATION_FAILED if issues else STOP_ANSWERED)
        final = self._merge_warnings(answer, warnings)
        return {"final": final, "stop_reason": stop, "warnings": warnings, "route": "done"}

    # ================================================================ 辅助

    def _request(
        self,
        messages: list[Message],
        tools: list[ToolSchema],
        *,
        response_schema: type[BaseModel] | None = None,
    ) -> ChatRequest:
        return ChatRequest(
            messages=messages,
            tools=tools,
            response_schema=response_schema,
            temperature=self.config.temperature,
            max_tokens=self.config.max_tokens,
            timeout_s=self.config.model_timeout_s,
        )

    async def _chat(
        self, state: AgentState, req: ChatRequest, updates: dict[str, Any], *, phase: str
    ) -> ChatResponse:
        """调用模型并把计数、usage、追踪事件写入 updates。异常向上抛出（计数仍记录）。"""
        model_calls = updates.get("model_calls", state["model_calls"]) + 1
        updates["model_calls"] = model_calls
        started = time.perf_counter()
        try:
            resp = await self.model.chat(req, task=self.config.task)
        except ModelError as e:
            self.tracer.event(
                state["run_id"],
                "model_call",
                {
                    "phase": phase,
                    "task": self.config.task,
                    "ok": False,
                    "error": _summ(f"{type(e).__name__}: {e}"),
                    "latency_ms": int((time.perf_counter() - started) * 1000),
                    "structured": req.response_schema is not None,
                },
            )
            raise
        usage = Usage.model_validate(updates.get("usage", state["usage"])) + resp.usage
        updates["usage"] = usage.model_dump()
        self.tracer.event(
            state["run_id"],
            "model_call",
            {
                "phase": phase,
                "task": self.config.task,
                "model": resp.model,
                "ok": True,
                "input_tokens": resp.usage.input_tokens,
                "output_tokens": resp.usage.output_tokens,
                "latency_ms": resp.latency_ms or int((time.perf_counter() - started) * 1000),
                "tool_calls": [c.name for c in resp.tool_calls],
                "structured": req.response_schema is not None,
                "content": _summ(resp.content or ""),
            },
        )
        return resp

    async def _invoke_tool(
        self, run_id: str, ctx: ToolContext, call: ToolCall, allowed: set[str]
    ) -> ToolResult:
        started = time.perf_counter()
        result = await self.registry.invoke(ctx, call, allowed=allowed)
        self.tracer.event(
            run_id,
            "tool_call",
            {
                "name": call.name,
                "call_id": call.id,
                "args": _summ(call.raw_arguments if call.arguments_error else call.arguments),
                "ok": result.ok,
                "error": result.error,
                "truncated": result.truncated,
                "latency_ms": int((time.perf_counter() - started) * 1000),
                "refs": len(result.refs),
                "output": _summ(result.to_message_content()),
                "artifact_ids": [
                    a["artifact_id"]
                    for a in (result.data or {}).get("artifacts", [])
                    if isinstance(a, dict) and "artifact_id" in a
                ],
                "content_needs_review": (result.data or {}).get("needs_review"),
            },
        )
        return result

    def _error_update(self, state: AgentState, error: str, *, draft: str | None = None) -> dict[str, Any]:
        warnings = [*state["warnings"], error]
        return {
            "stop_reason": STOP_ERROR,
            "error": error,
            "warnings": warnings,
            "final": self._fallback_answer(draft or "运行出错，未能给出答案", warnings),
            "route": "error",
        }

    def _fallback_answer(self, text: str, warnings: list[str]) -> dict[str, Any]:
        """运行时生成的兜底答案；answer_model 不接受时退回通用结构。"""
        raw = {"answer": text, "citations": [], "warnings": list(warnings)}
        try:
            return self.config.answer_model.model_validate(raw).model_dump()
        except ValidationError:
            return raw

    @staticmethod
    def _merge_warnings(answer: BaseModel, warnings: list[str]) -> dict[str, Any]:
        data = answer.model_dump()
        if isinstance(data.get("warnings"), list):
            merged = list(data["warnings"])
            merged.extend(w for w in warnings if w not in merged)
            data["warnings"] = merged
        return data
