from __future__ import annotations

import json

from drugpaper.agents.lookup.batch import DocLookup
from drugpaper.agents.lookup.models import LookupQuestion
from drugpaper.agents.reader.graph import PROMPTS
from drugpaper.agents.reader.notes import ReaderNotes, compact
from drugpaper.capabilities.tokens import estimate_tokens
from drugpaper.facts.full_text import FullTextContext
from drugpaper.facts.paths import PathProposal
from drugpaper.gateway.base import ChatRequest, Message, ModelClient
from drugpaper.services import Services


async def synthesize(
    services: Services,
    ctx: FullTextContext,
    notes: ReaderNotes,
    *,
    model: ModelClient | None = None,
    feedback: list[str] | None = None,
    confirmation_questions: list[LookupQuestion] | None = None,
) -> PathProposal:
    questions = {}
    for q in [*notes.open_questions, *(confirmation_questions or [])]:
        questions[q.question] = q
    # A single explicit confirmation batch per synthesis attempt; leftovers stay auditable.
    if questions:
        answers = await DocLookup(services, model).lookup(list(questions.values())[:8], ctx)
        notes.model_calls += answers.model_calls
        notes.tool_calls += answers.tool_calls
        notes.model_records.extend(answers.model_records)
        notes.lookup_answers.extend(answers.results)
    model = model or (await services.skills()).models
    dossier = []
    for compound in ctx.compounds:
        structure = compound.get("accepted_structure") or {}
        dossier.append(
            {
                "compound_id": compound["compound_id"],
                "label": compound["primary_label"],
                "aliases": compound.get("aliases", []),
                "role": compound.get("role"),
                "smiles_core": structure.get("smiles_core"),
                "formula": structure.get("formula"),
                "evidence": compound.get("evidence", []),
                "measurements": [
                    m
                    for m in ctx.measurements
                    if m.get("compound_id") == compound["compound_id"]
                    or m.get("subject_label") == compound["primary_label"]
                ][:5],
            }
        )
    statements = [
        {"node_ref": ref, **s}
        for ref, ex in ctx.extractions.items()
        for s in ex.get("optimization_statements", [])
    ]
    payload = {
        "notes": compact(notes, max_tokens=6000).model_dump(mode="json"),
        "compounds": dossier,
        "optimization_statements": statements,
        "route_reports": [
            {
                "node_ref": ref,
                "label": next((n.label for n in ctx.ir.nodes if n.ref == ref), None),
                "extraction": r.get("extraction", {}),
            }
            for ref, r in ctx.reports.items()
            if r.get("image_type") in {"scheme", "reaction", "optimization"}
        ],
        "meta": ctx.ir.meta.model_dump(),
        "feedback": feedback or [],
    }
    if estimate_tokens(json.dumps(payload, ensure_ascii=False)) > 12000:
        for c in dossier:
            c["measurements"] = c["measurements"][:2]
        payload["notes"] = compact(notes, max_tokens=4000).model_dump(mode="json")
    if estimate_tokens(json.dumps(payload, ensure_ascii=False)) > 12000:
        raise ValueError("synthesis dossier exceeds 12000 tokens; no evidence silently truncated")
    notes.model_calls += 1
    response = await model.chat(
        ChatRequest(
            messages=[
                Message(role="system", content=(PROMPTS / "reader_synthesize" / "task.md").read_text()),
                Message(role="user", content=json.dumps(payload, ensure_ascii=False)),
            ],
            response_schema=PathProposal,
            max_tokens=6000,
            timeout_s=180,
            thinking=True,
        ),
        task="reader_synthesize",
    )
    notes.model_records.append(
        {"task": "reader_synthesize", "model": response.model, "usage": response.usage.model_dump()}
    )
    return PathProposal.model_validate(response.parsed or {})
