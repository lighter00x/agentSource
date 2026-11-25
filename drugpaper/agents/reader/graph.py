"""Sequential reading with bounded lookups and durable SQL checkpoints per page."""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field
from sqlalchemy import func, select

from drugpaper.agents.lookup.batch import DocLookup
from drugpaper.agents.lookup.models import LookupResult
from drugpaper.agents.reader.notes import NoteDelta, ReaderNotes, compact, merge_delta
from drugpaper.agents.reader.page_pack import PagePack, build_page_pack
from drugpaper.agents.reader.queue import enqueue, should_flush
from drugpaper.domain.ids import new_id
from drugpaper.facts.full_text import FullTextContext, load_context
from drugpaper.gateway.base import ChatRequest, Message, ModelClient
from drugpaper.persistence.models import Document, ReaderNoteRow, RunRow, utcnow
from drugpaper.services import Services

PROMPTS = Path(__file__).resolve().parents[3] / "prompts" / "skills"


class ReaderProgress(BaseModel):
    status: Literal["completed", "pending", "failed"]
    notes: ReaderNotes
    last_page: int | None = None
    pending_refs: list[str] = Field(default_factory=list)
    checkpoint_id: str | None = None
    run_id: str
    error: str | None = None


class ReaderAgent:
    def __init__(self, services: Services, model: ModelClient | None = None) -> None:
        self.services, self.model = services, model

    async def reread(self, document_id: str, from_page: int, *, user_id: str = "default") -> ReaderProgress:
        return await self.read(document_id, user_id=user_id, from_page=from_page)

    async def _restore(self, ctx: FullTextContext, from_page: int | None) -> tuple[ReaderNotes, str | None]:
        pages = sorted(p.page for p in ctx.ir.pages)
        if from_page is not None and from_page not in pages:
            raise ValueError("reread page outside document")
        async with self.services.db.session(ctx.user_id) as session:
            where = [
                ReaderNoteRow.user_id == ctx.user_id,
                ReaderNoteRow.document_id == ctx.ir.document_id,
                ReaderNoteRow.revision == ctx.ir.revision,
                ReaderNoteRow.fingerprint == ctx.fingerprint,
                ReaderNoteRow.is_active.is_(True),
            ]
            if from_page is not None:
                rows = (
                    await session.execute(
                        select(ReaderNoteRow).where(*where, ReaderNoteRow.page >= from_page)
                    )
                ).scalars()
                for obsolete in rows:
                    obsolete.is_active = False
                where.append(ReaderNoteRow.page < from_page)
            row = (
                await session.execute(
                    select(ReaderNoteRow)
                    .where(*where)
                    .order_by(ReaderNoteRow.page.desc(), ReaderNoteRow.version.desc())
                    .limit(1)
                )
            ).scalar_one_or_none()
            if (
                from_page is not None
                and from_page != pages[0]
                and (row is None or row.page != pages[pages.index(from_page) - 1])
            ):
                raise ValueError("previous-page checkpoint unavailable for this revision/fingerprint")
            return (
                (ReaderNotes.model_validate(row.payload["notes"]), row.id) if row else (ReaderNotes(), None)
            )

    async def _flush(self, notes: ReaderNotes, ctx: FullTextContext) -> list[LookupResult]:
        answers = []
        lookup = DocLookup(self.services, self.model)
        queue = list(notes.open_questions)
        # Larger queues are split into explicit batches; no nested lookup agent calls.
        for offset in range(0, len(queue), 8):
            out = await lookup.lookup(queue[offset : offset + 8], ctx)
            notes.model_calls += out.model_calls
            notes.tool_calls += out.tool_calls
            notes.model_records.extend(out.model_records)
            notes.warnings.extend(out.warnings)
            answers.extend(out.results)
        notes.lookup_answers.extend(answers)
        resolved = [a.question for a in answers if a.status == "found"]
        notes.open_questions = enqueue(notes.open_questions, [], resolved)
        for a in answers:
            for e in a.evidence:
                node = next(n for n in ctx.ir.nodes if n.ref == e.node_id)
                notes.pointers.setdefault(e.node_id, []).extend([node.page] if node.page is not None else [])
        return answers

    async def _checkpoint(
        self,
        ctx: FullTextContext,
        notes: ReaderNotes,
        pack: PagePack,
        deltas: list[dict[str, Any]],
        predecessor: str | None,
    ) -> str:
        refreshed = await load_context(self.services.db, ctx.ir.document_id, user_id=ctx.user_id)
        if refreshed.fingerprint != ctx.fingerprint:
            raise ValueError("source changed during reading; checkpoint rejected")
        async with self.services.db.session(ctx.user_id) as session:
            # Document lock serializes version allocation on PostgreSQL; SQLite serializes writes.
            await session.execute(
                select(Document.id).where(Document.id == ctx.ir.document_id).with_for_update()
            )
            latest = (
                await session.execute(
                    select(ReaderNoteRow)
                    .where(
                        ReaderNoteRow.user_id == ctx.user_id,
                        ReaderNoteRow.document_id == ctx.ir.document_id,
                        ReaderNoteRow.revision == ctx.ir.revision,
                        ReaderNoteRow.fingerprint == ctx.fingerprint,
                        ReaderNoteRow.is_active.is_(True),
                    )
                    .order_by(ReaderNoteRow.page.desc(), ReaderNoteRow.version.desc())
                    .limit(1)
                )
            ).scalar_one_or_none()
            if (latest.id if latest else None) != predecessor:
                raise ValueError("reader checkpoint advanced concurrently; resume required")
            version = (
                await session.execute(
                    select(func.max(ReaderNoteRow.version)).where(
                        ReaderNoteRow.user_id == ctx.user_id,
                        ReaderNoteRow.document_id == ctx.ir.document_id,
                        ReaderNoteRow.revision == ctx.ir.revision,
                        ReaderNoteRow.page == pack.page,
                    )
                )
            ).scalar() or 0
            row_id = new_id("notes")
            session.add(
                ReaderNoteRow(
                    id=row_id,
                    user_id=ctx.user_id,
                    document_id=ctx.ir.document_id,
                    revision=ctx.ir.revision,
                    fingerprint=ctx.fingerprint,
                    page=pack.page,
                    version=version + 1,
                    payload={
                        "schema_version": "0.6.1",
                        "notes": notes.model_dump(mode="json"),
                        "deltas": deltas,
                        "page_mode": pack.mode,
                        "predecessor": predecessor,
                    },
                )
            )
        return row_id

    async def read(
        self,
        document_id: str,
        *,
        user_id: str = "default",
        from_page: int | None = None,
        wait_timeout_s: float = 0,
    ) -> ReaderProgress:
        ctx = await load_context(self.services.db, document_id, user_id=user_id)
        notes, checkpoint = await self._restore(ctx, from_page)
        run_id = new_id("run")
        out = ReaderProgress(
            status="completed",
            notes=notes,
            last_page=max(notes.pages_read, default=None),
            checkpoint_id=checkpoint,
            run_id=run_id,
        )
        async with self.services.db.session(user_id) as session:
            session.add(
                RunRow(
                    id=run_id,
                    user_id=user_id,
                    document_id=document_id,
                    run_type="reader",
                    component="reader_read",
                    component_version="0.6.1",
                    component_hash=ctx.component_version,
                    trace_id=run_id,
                    inputs={"fingerprint": ctx.fingerprint, "from_page": from_page},
                )
            )
        try:
            for page in sorted(p.page for p in ctx.ir.pages):
                if page in notes.pages_read:
                    continue
                pack = build_page_pack(ctx.ir, page, reports=ctx.reports, extractions=ctx.extractions)
                deadline = time.monotonic() + min(max(wait_timeout_s, 0), 60)
                while not pack.ready and time.monotonic() < deadline:
                    await asyncio.sleep(min(1, max(0, deadline - time.monotonic())))
                    new_ctx = await load_context(self.services.db, document_id, user_id=user_id)
                    # Ready dependencies may arrive while waiting; no already-read page is reused under a new fingerprint.
                    if notes.pages_read and new_ctx.fingerprint != ctx.fingerprint:
                        raise ValueError("reading dependencies changed; restart with fresh checkpoints")
                    ctx = new_ctx
                    pack = build_page_pack(ctx.ir, page, reports=ctx.reports, extractions=ctx.extractions)
                if not pack.ready:
                    out.status, out.pending_refs = "pending", pack.pending_refs
                    break
                previous_page = max(notes.pages_read, default=None)
                previous_roles = {
                    n.section_role
                    for n in ctx.ir.nodes
                    if n.page == previous_page and n.node_type == "paragraph"
                }
                roles = {
                    n.section_role for n in ctx.ir.nodes if n.page == page and n.node_type == "paragraph"
                }
                attached = list(notes.lookup_answers[-8:])
                if should_flush(
                    notes.open_questions, chapter_boundary=bool(previous_roles and roles != previous_roles)
                ):
                    attached = await self._flush(notes, ctx)
                deltas = []
                if pack.mode == "read":
                    model = self.model or (await self.services.skills()).models
                    for unit in pack.units:
                        prompt = {
                            "page": page,
                            "unit": unit.model_dump(),
                            "notes": notes.model_dump(),
                            "lookup_answers": [a.model_dump(mode="json") for a in attached],
                            "compound_labels": [c["primary_label"] for c in ctx.compounds],
                        }
                        notes.model_calls += 1
                        response = await model.chat(
                            ChatRequest(
                                messages=[
                                    Message(
                                        role="system",
                                        content=(PROMPTS / "reader_page" / "task.md").read_text(),
                                    ),
                                    Message(role="user", content=json.dumps(prompt, ensure_ascii=False)),
                                ],
                                response_schema=NoteDelta,
                                max_tokens=4096,
                                timeout_s=120,
                            ),
                            task="reader_page",
                        )
                        notes.model_records.append(
                            {
                                "task": "reader_page",
                                "model": response.model,
                                "usage": response.usage.model_dump(),
                            }
                        )
                        delta = NoteDelta.model_validate(response.parsed or {})
                        # A reading unit, rather than the entire page, is the evidence disclosure boundary.
                        unit_pack = pack.model_copy(update={"units": [unit]})
                        labels = {
                            label
                            for c in ctx.compounds
                            for label in [c["primary_label"], *c.get("aliases", [])]
                        }
                        rejected = merge_delta(notes, delta, unit_pack, labels, attached)
                        deltas.append(delta.model_dump(mode="json"))
                        naturally_resolved = delta.resolved_questions if not rejected else []
                        notes.open_questions = enqueue(
                            notes.open_questions, delta.open_questions, naturally_resolved
                        )
                        notes = compact(notes)
                elif pack.mode == "skim":
                    # Experimental facts already live in T1/chemical ledgers; no second transcription.
                    for unit in pack.units:
                        for item in unit.items:
                            if item.get("node_ref"):
                                notes.pointers.setdefault(item["node_ref"], []).append(page)
                notes.pages_read.append(page)
                notes.warnings = list(dict.fromkeys([*notes.warnings, *pack.warnings]))
                if should_flush(notes.open_questions, end=page == max(p.page for p in ctx.ir.pages)):
                    await self._flush(notes, ctx)
                notes = compact(notes)
                checkpoint = await self._checkpoint(ctx, notes, pack, deltas, checkpoint)
                out.notes, out.last_page, out.checkpoint_id = notes, page, checkpoint
        except Exception as exc:
            out.status = "failed"
            out.error = f"{type(exc).__name__}: {exc}"[:500]
        async with self.services.db.session(user_id) as session:
            run = await session.get(RunRow, run_id)
            assert run is not None
            run.status, run.ended_at = out.status, utcnow()
            run.outputs = out.model_dump(mode="json")
            run.model = {"calls": notes.model_records}
            run.metrics = {
                "model_calls": notes.model_calls,
                "tool_calls": notes.tool_calls,
                "pages_read": len(notes.pages_read),
            }
            run.error = {"message": out.error} if out.error else None
        return out
