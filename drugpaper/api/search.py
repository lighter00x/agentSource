"""检索接口：`GET /documents/{id}/search?q=&mode=grep|fts|trgm|hybrid`。"""

from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Query

from drugpaper.api.deps import SessionDep, require_ir
from drugpaper.api.errors import ApiError, ErrorResponse
from drugpaper.api.schemas import SearchResponse, SearchResultItem
from drugpaper.persistence.repos.nodes import load_ir
from drugpaper.text.grep import GrepEngine, GrepError, GrepQuery
from drugpaper.text.index import InMemorySearch, PostgresSearch, SearchBackend

router = APIRouter(tags=["search"], responses={400: {"model": ErrorResponse}, 404: {"model": ErrorResponse}})


@router.get("/documents/{document_id}/search", response_model=SearchResponse)
async def search_document(
    s: SessionDep,
    document_id: str,
    q: Annotated[str, Query(min_length=1, max_length=500)],
    mode: Annotated[Literal["grep", "fts", "trgm", "hybrid"], Query()] = "hybrid",
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    literal: Annotated[bool, Query(description="grep：按字面匹配")] = False,
) -> SearchResponse:
    doc = await require_ir(s, document_id)
    rev = doc.active_revision or 1
    if mode == "grep":
        ir = await load_ir(s, document_id, rev)
        try:
            res = GrepEngine(ir.nodes).grep(GrepQuery(pattern=q, literal=literal, max_results=limit))
        except GrepError as e:
            raise ApiError(400, "BAD_PATTERN", str(e)) from e
        items = [
            SearchResultItem(
                node_id=m.node_id,
                page=m.page,
                type=m.node_type,
                section=m.section,
                snippet=m.snippet,
                char_span=m.char_span,
            )
            for m in res.matches
        ]
        return SearchResponse(
            document_id=document_id, query=q, mode=mode, results=items, truncated=res.truncated
        )
    backend: SearchBackend
    if s.get_bind().dialect.name == "postgresql":
        backend = PostgresSearch(s, document_id, rev)
    else:
        backend = InMemorySearch((await load_ir(s, document_id, rev)).nodes)
    hits = await backend.search(q, mode, limit)
    items = [
        SearchResultItem(
            node_id=h.node_id,
            page=h.page,
            type=h.node_type,
            section=h.section,
            snippet=h.snippet,
            score=h.score,
        )
        for h in hits
    ]
    return SearchResponse(document_id=document_id, query=q, mode=mode, results=items)
