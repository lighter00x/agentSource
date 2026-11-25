from __future__ import annotations

import re
import unicodedata

from sqlalchemy import select

from drugpaper.agents.lookup.models import LookupResult
from drugpaper.facts.full_text import FullTextContext
from drugpaper.persistence.db import Database
from drugpaper.persistence.models import LookupRow


def normalize_question(question: str) -> str:
    return re.sub(r"[\s?？。]+", "", unicodedata.normalize("NFKC", question).casefold())


def cache_key(question: str, kind: str = "text", page: int | None = None) -> str:
    return normalize_question(question) + (f"|vision:{page}" if kind == "vision" else "")


async def cached(
    db: Database, ctx: FullTextContext, question: str, *, kind: str = "text", page: int | None = None
) -> LookupResult | None:
    async with db.session(ctx.user_id) as session:
        row = (
            await session.execute(
                select(LookupRow)
                .where(
                    LookupRow.user_id == ctx.user_id,
                    LookupRow.document_id == ctx.ir.document_id,
                    LookupRow.revision == ctx.ir.revision,
                    LookupRow.fingerprint == ctx.fingerprint,
                    LookupRow.question_key == cache_key(question, kind, page),
                )
                .order_by(LookupRow.created_at.desc(), LookupRow.id.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        return LookupResult.model_validate(row.payload) if row else None
