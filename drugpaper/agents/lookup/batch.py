"""DocLookup: deterministic fast paths, one shared plan/answer, bounded read-only tools."""

from __future__ import annotations

import asyncio
import json
from collections import defaultdict

from drugpaper.agents.lookup.cache import cache_key, cached
from drugpaper.agents.lookup.fast_path import fast_action, format_fast
from drugpaper.agents.lookup.models import (
    LookupAction,
    LookupAnswers,
    LookupBatchResult,
    LookupPlan,
    LookupQuestion,
    LookupResult,
)
from drugpaper.agents.lookup.tools import READ_ONLY, lookup_tools
from drugpaper.agents.lookup.vision import vision_page
from drugpaper.capabilities.index import CapabilityIndex
from drugpaper.capabilities.router import CapabilityRouter, DisclosureContext
from drugpaper.domain.ids import new_id
from drugpaper.facts.full_text import FullTextContext, load_context, valid_evidence
from drugpaper.gateway.base import ChatRequest, Message, ModelClient, ToolCall
from drugpaper.persistence.models import LookupRow, RunRow, utcnow
from drugpaper.services import Services
from drugpaper.tools.protocol import ToolContext, ToolResult
from drugpaper.tools.registry import truncate_result


class DocLookup:
    def __init__(self, services: Services, model: ModelClient | None = None) -> None:
        self.services, self.model = services, model

    async def lookup(self, questions: list[LookupQuestion], ctx: FullTextContext) -> LookupBatchResult:
        if not 1 <= len(questions) <= 8:
            raise ValueError("lookup batch must contain 1..8 questions")
        current = await load_context(self.services.db, ctx.ir.document_id, user_id=ctx.user_id)
        if current.fingerprint != ctx.fingerprint:
            raise ValueError("lookup context is stale")
        run_id = new_id("run")
        out = LookupBatchResult(batch_run_id=run_id)
        run = RunRow(
            id=run_id,
            user_id=ctx.user_id,
            document_id=ctx.ir.document_id,
            run_type="lookup",
            component="doc_lookup",
            component_version="0.6.1",
            component_hash=ctx.component_version,
            inputs={"questions": [q.model_dump() for q in questions], "fingerprint": ctx.fingerprint},
            trace_id=run_id,
        )
        async with self.services.db.session(ctx.user_id) as session:
            session.add(run)
        results: dict[str, LookupResult] = {}
        unique: dict[str, LookupQuestion] = {}
        for q in questions:
            unique.setdefault(cache_key(q.question, q.kind, q.target_page), q)
        pending: list[tuple[str, LookupQuestion]] = []
        error: Exception | None = None
        try:
            async with asyncio.timeout(60):
                for key, q in unique.items():
                    hit = await cached(self.services.db, ctx, q.question, kind=q.kind, page=q.target_page)
                    if hit and hit.status != "need_vision":
                        results[key] = hit
                        out.cache_hits += 1
                    else:
                        pending.append((key, q))
                await self._execute(pending, ctx, out, results)
                refreshed = await load_context(self.services.db, ctx.ir.document_id, user_id=ctx.user_id)
                if refreshed.fingerprint != ctx.fingerprint:
                    raise ValueError("source changed during lookup")
        except Exception as exc:
            error = exc
            out.warnings.append(f"{type(exc).__name__}: {exc}"[:300])
        for key, q in unique.items():
            if key not in results:
                results[key] = LookupResult(
                    question=q.question,
                    status="need_vision" if q.kind == "vision" else "not_found",
                    answer="回查未完成，需重试。",
                    lookup_run_id=run_id,
                )
        out.results = [
            results[cache_key(q.question, q.kind, q.target_page)].model_copy(update={"question": q.question})
            for q in questions
        ]
        async with self.services.db.session(ctx.user_id) as session:
            stored = await session.get(RunRow, run_id)
            assert stored is not None
            stored.status = "failed" if error else "completed"
            stored.ended_at = utcnow()
            stored.outputs = out.model_dump(mode="json")
            stored.model = {"calls": out.model_records}
            stored.metrics = {
                "model_calls": out.model_calls,
                "tool_calls": out.tool_calls,
                "cache_hits": out.cache_hits,
            }
            stored.error = {"message": out.warnings[-1]} if error else None
            if error is None:
                for key, _q in pending:
                    r = results[key]
                    if r.status != "need_vision":
                        session.add(
                            LookupRow(
                                id=new_id("lookup"),
                                user_id=ctx.user_id,
                                document_id=ctx.ir.document_id,
                                revision=ctx.ir.revision,
                                fingerprint=ctx.fingerprint,
                                question_key=key,
                                run_id=run_id,
                                payload=r.model_dump(mode="json"),
                            )
                        )
        return out

    async def _execute(
        self,
        pending: list[tuple[str, LookupQuestion]],
        ctx: FullTextContext,
        out: LookupBatchResult,
        results: dict[str, LookupResult],
    ) -> None:
        registry = lookup_tools(ctx)
        tc = ToolContext(
            run_id=out.batch_run_id,
            document_id=ctx.ir.document_id,
            user_id=ctx.user_id,
            trace_id=out.batch_run_id,
        )
        general = []
        evidence_by_question: dict[int, list[ToolResult]] = defaultdict(list)
        vision: dict[int, list[tuple[str, LookupQuestion]]] = defaultdict(list)
        for i, (key, q) in enumerate(pending):
            if q.kind == "vision" and q.target_page is not None:
                vision[q.target_page].append((key, q))
                continue
            action = fast_action(q, ctx, i)
            if action:
                result = await registry.invoke(
                    tc,
                    ToolCall(id=new_id("call"), name=action.tool, arguments=action.args),
                    allowed=set(READ_ONLY),
                )
                out.tool_calls += 1
                answer = format_fast(q, action, result)
                answer.evidence = [e for e in answer.evidence if valid_evidence(ctx.ir, e)]
                if answer.status == "found" and not answer.evidence:
                    answer.status = "not_found"
                answer.lookup_run_id = out.batch_run_id
                results[key] = answer
            else:
                general.append((i, key, q))
        model = self.model
        if general or vision:
            model = model or (await self.services.skills()).models
        if general:
            assert model is not None
            names = {"search", "get_node", "get_context_window"}
            for _, _, q in general:
                text = q.question.lower()
                if any(word in text for word in ("值", "指标", "activity", "herg")):
                    names.update({"query_measurements", "get_compound"})
                if any(word in text for word in ("图", "表", "figure", "table", "scheme")):
                    names.update({"get_figure_report", "get_table"})
                if "页" in text:
                    names.add("get_page_text")
            names &= READ_ONLY
            disclosure = CapabilityRouter(CapabilityIndex(None, registry)).preselect(
                DisclosureContext(
                    user_id=ctx.user_id,
                    audience="lookup",
                    intents=sorted(names),
                    token_budget=2200,
                    invocations=["tool_call"],
                    max_candidates=len(names),
                )
            )
            disclosed = {item.name for item in disclosure.items} & names
            prompt = {
                "questions": [{"question_id": i, **q.model_dump()} for i, _, q in general],
                "tools": [s.model_dump() for s in registry.schemas(sorted(disclosed))],
                "max_actions": 20 - out.tool_calls,
            }
            out.model_calls += 1
            response = await model.chat(
                ChatRequest(
                    messages=[
                        Message(
                            role="system",
                            content="为每题规划最多2个只读动作。仅选已列工具，不调用Skill或其他Agent。输出actions，不作答。",
                        ),
                        Message(role="user", content=json.dumps(prompt, ensure_ascii=False)),
                    ],
                    response_schema=LookupPlan,
                    max_tokens=1800,
                    timeout_s=60,
                ),
                task="lookup_plan",
            )
            out.model_records.append(
                {"task": "lookup_plan", "model": response.model, "usage": response.usage.model_dump()}
            )
            plan = LookupPlan.model_validate(response.parsed or {})
            valid_ids = {i for i, _, _ in general}
            actions: list[LookupAction] = []
            counts: dict[int, int] = defaultdict(int)
            for a in plan.actions:
                if (
                    a.question_id in valid_ids
                    and a.tool in disclosed
                    and counts[a.question_id] < 2
                    and len(actions) + out.tool_calls < 20
                ):
                    actions.append(a)
                    counts[a.question_id] += 1
                else:
                    out.warnings.append("rejected unsupported/over-budget lookup action")
            tool_results = await asyncio.gather(
                *(
                    registry.invoke(
                        tc, ToolCall(id=new_id("call"), name=a.tool, arguments=a.args), allowed=disclosed
                    )
                    for a in actions
                )
            )
            out.tool_calls += len(actions)
            for a, r in zip(actions, tool_results, strict=True):
                evidence_by_question[a.question_id].append(truncate_result(r, 3500))
            answer_input = [
                {
                    "question": q.question,
                    "question_id": i,
                    "tool_results": [r.model_dump(mode="json") for r in evidence_by_question[i]],
                }
                for i, _, q in general
            ]
            out.model_calls += 1
            response = await model.chat(
                ChatRequest(
                    messages=[
                        Message(
                            role="system",
                            content="逐题回答，仅使用该题工具结果。找不到写not_found，冲突写ambiguous，需读图写need_vision与页码；found必须给工具提供的证据，不得猜数值。答案不超过150字。question逐字保留。",
                        ),
                        Message(role="user", content=json.dumps(answer_input, ensure_ascii=False)),
                    ],
                    response_schema=LookupAnswers,
                    max_tokens=2400,
                    timeout_s=60,
                ),
                task="lookup_answer",
            )
            out.model_records.append(
                {"task": "lookup_answer", "model": response.model, "usage": response.usage.model_dump()}
            )
            answers = LookupAnswers.model_validate(response.parsed or {})
            for i, key, q in general:
                matched = [a for a in answers.results if a.question == q.question]
                answer = matched[0] if len(matched) == 1 else LookupResult(question=q.question)
                refs = {r for result in evidence_by_question[i] if result.ok for r in result.refs}
                answer.evidence = [
                    e for e in answer.evidence if e.node_id in refs and valid_evidence(ctx.ir, e)
                ]
                answer.suggested_pages = [
                    p for p in answer.suggested_pages if p in {page.page for page in ctx.ir.pages}
                ]
                if answer.status == "found" and not answer.evidence:
                    answer = LookupResult(
                        question=q.question, status="not_found", answer="没有可核验的工具证据。"
                    )
                answer.lookup_run_id = out.batch_run_id
                results[key] = answer
                if answer.status == "need_vision":
                    page = (
                        q.target_page
                        if q.target_page is not None
                        else next(iter(answer.suggested_pages), None)
                    )
                    if page is not None:
                        vision[page].append((key, q))
        if vision:
            assert model is not None
            for page, group in vision.items():
                previous_records = len(out.model_records)
                try:
                    answered = await vision_page(
                        self.services, model, ctx, page, [q for _, q in group], out.model_records
                    )
                except (ValueError, TimeoutError) as exc:
                    out.warnings.append(f"vision unavailable: {exc}")
                    answered = [
                        LookupResult(question=q.question, status="need_vision", suggested_pages=[page])
                        for _, q in group
                    ]
                for (key, _q), answer in zip(group, answered, strict=True):
                    answer.lookup_run_id = out.batch_run_id
                    results[key] = answer
                out.model_calls += len(out.model_records) - previous_records


async def lookup(
    questions: list[LookupQuestion], ctx: FullTextContext, services: Services
) -> LookupBatchResult:
    return await DocLookup(services).lookup(questions, ctx)
