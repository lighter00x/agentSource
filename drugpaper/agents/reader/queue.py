from __future__ import annotations

from drugpaper.agents.lookup.cache import normalize_question
from drugpaper.agents.lookup.models import LookupQuestion


def enqueue(
    current: list[LookupQuestion], incoming: list[LookupQuestion], resolved: list[str]
) -> list[LookupQuestion]:
    done = {normalize_question(q) for q in resolved}
    unique: dict[str, LookupQuestion] = {}
    for q in [*current, *incoming]:
        key = normalize_question(q.question)
        if key not in done:
            old = unique.get(key)
            unique[key] = (
                q if old is None else old.model_copy(update={"blocking": old.blocking or q.blocking})
            )
    return list(unique.values())


def should_flush(queue: list[LookupQuestion], *, chapter_boundary: bool = False, end: bool = False) -> bool:
    return bool(queue) and (len(queue) >= 5 or any(q.blocking for q in queue) or chapter_boundary or end)
