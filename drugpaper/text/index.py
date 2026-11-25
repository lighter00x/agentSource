"""检索：全文检索（fts）、三元组模糊检索（trgm）与混合（hybrid）（执行方案 S2.8）。

- PostgreSQL：`nodes.tsv`（`to_tsvector('simple', label || text_norm)`）+ `websearch_to_tsquery`；
  `pg_trgm` 可用时用 `word_similarity`，不可用时（如嵌入式测试库）退回 Python 实现；
- SQLite / 无数据库：全部在 Python 中实现（`InMemorySearch`），语义与 PG 版保持一致（AND 语义、按分数排序）。
"""

from __future__ import annotations

import math
import re
from collections import Counter
from collections.abc import Iterable
from typing import Any, Literal, Protocol

from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from drugpaper.domain.ids import global_ref
from drugpaper.domain.nodes import DocumentNode
from drugpaper.text.grep import DEFAULT_EXCLUDED_SECTIONS, SEARCHABLE_TYPES, make_snippet

SearchMode = Literal["fts", "trgm", "hybrid"]
_TOKEN = re.compile(r"[A-Za-z0-9α-ωΑ-Ω]+")
TRGM_THRESHOLD = 0.3


class SearchHit(BaseModel):
    node_id: str
    local_id: str
    page: int | None
    node_type: str
    section: str | None
    score: float
    snippet: str


def tokens(s: str) -> list[str]:
    return [t.lower() for t in _TOKEN.findall(s)]


def trigrams(s: str) -> set[str]:
    """与 pg_trgm 一致：每个词前补两个空格、后补一个空格后取三元组。"""
    out: set[str] = set()
    for w in tokens(s):
        padded = f"  {w} "
        out.update(padded[i : i + 3] for i in range(len(padded) - 2))
    return out


def word_similarity(query: str, text_: str) -> float:
    """近似 pg_trgm 的 word_similarity：query 的三元组与 text 中最相近的连续词窗口的相似度。"""
    q = trigrams(query)
    if not q:
        return 0.0
    words = tokens(text_)
    n = max(1, len(tokens(query)))
    best = 0.0
    for i in range(len(words)):
        window = " ".join(words[i : i + n])
        w = trigrams(window)
        if not w:
            continue
        sim = len(q & w) / len(q | w)
        if sim > best:
            best = sim
            if best >= 0.999:
                break
    return best


class SearchBackend(Protocol):
    async def search(self, query: str, mode: SearchMode = "hybrid", limit: int = 10) -> list[SearchHit]: ...


def _hit(n: DocumentNode, score: float, query: str) -> SearchHit:
    text_ = n.text_norm or n.text_raw or ""
    toks = tokens(query)
    pos = next((text_.lower().find(t) for t in toks if t and text_.lower().find(t) >= 0), 0)
    return SearchHit(
        node_id=n.ref,
        local_id=n.local_id,
        page=n.page,
        node_type=n.node_type,
        section=n.section_role,
        score=round(score, 4),
        snippet=make_snippet(text_, pos, pos + 1, 0)[:300] if text_ else (n.label or ""),
    )


def merge_hybrid(fts: list[SearchHit], trgm: list[SearchHit], limit: int) -> list[SearchHit]:
    """两路分数各自归一化到 [0,1] 后相加（fts 权重 0.6，trgm 0.4）。"""
    out: dict[str, SearchHit] = {}
    for hits, weight in ((fts, 0.6), (trgm, 0.4)):
        top = max((h.score for h in hits), default=0.0) or 1.0
        for h in hits:
            s = weight * h.score / top
            if h.node_id in out:
                out[h.node_id] = out[h.node_id].model_copy(
                    update={"score": round(out[h.node_id].score + s, 4)}
                )
            else:
                out[h.node_id] = h.model_copy(update={"score": round(s, 4)})
    return sorted(out.values(), key=lambda h: -h.score)[:limit]


class InMemorySearch:
    """Python 实现：fts 为 AND 语义的 TF-IDF，trgm 为三元组词相似度。"""

    def __init__(self, nodes: Iterable[DocumentNode]) -> None:
        self._nodes = [
            n
            for n in nodes
            if n.node_type in SEARCHABLE_TYPES
            and (n.text_norm or n.text_raw)
            and n.section_role not in DEFAULT_EXCLUDED_SECTIONS
        ]
        self._tf = [
            Counter(tokens(f"{n.label or ''} {n.text_norm or n.text_raw or ''}")) for n in self._nodes
        ]
        df: Counter[str] = Counter()
        for tf in self._tf:
            df.update(tf.keys())
        self._idf = {t: math.log(1 + len(self._nodes) / c) for t, c in df.items()}

    def fts(self, query: str, limit: int) -> list[SearchHit]:
        q = [t for t in tokens(query) if t]
        if not q:
            return []
        scored = []
        for n, tf in zip(self._nodes, self._tf, strict=True):
            if not all(t in tf for t in q):
                continue
            length = sum(tf.values()) or 1
            score = sum((tf[t] / length) * self._idf.get(t, 0.0) for t in q) * 100
            scored.append((score, n))
        scored.sort(key=lambda x: (-x[0], x[1].reading_order))
        return [_hit(n, s, query) for s, n in scored[:limit]]

    def trgm(self, query: str, limit: int) -> list[SearchHit]:
        scored = []
        for n in self._nodes:
            s = word_similarity(query, n.text_norm or n.text_raw or "")
            if s >= TRGM_THRESHOLD:
                scored.append((s, n))
        scored.sort(key=lambda x: (-x[0], x[1].reading_order))
        return [_hit(n, s, query) for s, n in scored[:limit]]

    async def search(self, query: str, mode: SearchMode = "hybrid", limit: int = 10) -> list[SearchHit]:
        if mode == "fts":
            return self.fts(query, limit)
        if mode == "trgm":
            return self.trgm(query, limit)
        return merge_hybrid(self.fts(query, limit * 2), self.trgm(query, limit * 2), limit)


_SEARCHABLE_SQL = "(" + ",".join(f"'{t}'" for t in sorted(SEARCHABLE_TYPES)) + ")"


class PostgresSearch:
    """PostgreSQL 实现。需要迁移 0002（tsv 列）；pg_trgm 不可用时 trgm 模式退回 Python。"""

    def __init__(self, session: AsyncSession, document_id: str, revision: int) -> None:
        self.s = session
        self.document_id = document_id
        self.revision = revision
        self._has_trgm: bool | None = None
        self._fallback: InMemorySearch | None = None

    async def has_trgm(self) -> bool:
        if self._has_trgm is None:
            r = await self.s.execute(text("SELECT 1 FROM pg_extension WHERE extname = 'pg_trgm'"))
            self._has_trgm = r.first() is not None
        return self._has_trgm

    def _row_hit(self, row: Any, query: str) -> SearchHit:
        local_id: str = row[0]
        node_type: str = row[1]
        page: int | None = row[2]
        section: str | None = row[3]
        text_norm: str | None = row[4]
        label: str | None = row[5]
        score: float = row[6]
        body = text_norm or label or ""
        toks = tokens(query)
        pos = next((body.lower().find(t) for t in toks if body.lower().find(t) >= 0), 0)
        return SearchHit(
            node_id=global_ref(self.document_id, self.revision, local_id),
            local_id=local_id,
            page=page,
            node_type=node_type,
            section=section,
            score=round(float(score), 4),
            snippet=make_snippet(body, pos, pos + 1, 0)[:300],
        )

    async def fts(self, query: str, limit: int) -> list[SearchHit]:
        sql = text(
            f"""
            SELECT local_id, node_type, page, section_role, text_norm, label,
                   ts_rank_cd(tsv, websearch_to_tsquery('simple', :q)) * 10 AS score
            FROM nodes
            WHERE document_id = :d AND revision = :r AND node_type IN {_SEARCHABLE_SQL}
              AND coalesce(section_role, '') <> 'references'
              AND tsv @@ websearch_to_tsquery('simple', :q)
            ORDER BY score DESC, reading_order
            LIMIT :n
            """
        )
        rows = await self.s.execute(sql, {"q": query, "d": self.document_id, "r": self.revision, "n": limit})
        return [self._row_hit(r, query) for r in rows]

    async def trgm(self, query: str, limit: int) -> list[SearchHit]:
        if not await self.has_trgm():
            if self._fallback is None:
                from drugpaper.persistence.repos.nodes import load_ir

                ir = await load_ir(self.s, self.document_id, self.revision)
                self._fallback = InMemorySearch(ir.nodes)
            return self._fallback.trgm(query, limit)
        sql = text(
            f"""
            SELECT local_id, node_type, page, section_role, text_norm, label,
                   word_similarity(:q, text_norm) AS score
            FROM nodes
            WHERE document_id = :d AND revision = :r AND node_type IN {_SEARCHABLE_SQL}
              AND coalesce(section_role, '') <> 'references'
              AND word_similarity(:q, text_norm) >= :t
            ORDER BY score DESC, reading_order
            LIMIT :n
            """
        )
        rows = await self.s.execute(
            sql, {"q": query, "d": self.document_id, "r": self.revision, "n": limit, "t": TRGM_THRESHOLD}
        )
        return [self._row_hit(r, query) for r in rows]

    async def search(self, query: str, mode: SearchMode = "hybrid", limit: int = 10) -> list[SearchHit]:
        if mode == "fts":
            return await self.fts(query, limit)
        if mode == "trgm":
            return await self.trgm(query, limit)
        return merge_hybrid(await self.fts(query, limit * 2), await self.trgm(query, limit * 2), limit)
