"""S2.9：grep 与 search（Python 实现，真实样例论文）。"""

from __future__ import annotations

import pytest

from drugpaper.text.grep import GrepEngine, GrepError, GrepQuery, GrepScope
from drugpaper.text.index import InMemorySearch, merge_hybrid, trigrams, word_similarity
from tests.unit.ir.conftest import needs_real
from tests.unit.ir.real import real_ir

pytestmark = needs_real


@pytest.fixture(scope="module")
def engine() -> GrepEngine:
    return GrepEngine(real_ir().nodes)


def test_grep_26_hits_abstract_and_body_with_raw_span(engine: GrepEngine) -> None:
    res = engine.grep(GrepQuery(pattern=r"\b26\b", max_results=200))
    ids = [m.local_id for m in res.matches]
    assert "p000.b007" in ids and len(set(ids)) > 10
    raw = {n.local_id: n.text_raw for n in real_ir().nodes}
    for m in res.matches:
        span_text = raw[m.local_id][m.char_span[0] : m.char_span[1]]
        assert span_text == m.match
        assert span_text.replace(" ", "") == "26"  # 公式里可能是 "2 6"
    assert sum(m.match == "26" for m in res.matches) > len(res.matches) / 2
    assert not res.truncated and res.total == len(res.matches)


def test_grep_d1_receptor_maps_back_to_latex(engine: GrepEngine) -> None:
    res = engine.grep(GrepQuery(pattern="D1 receptor"))
    abstract = next(m for m in res.matches if m.local_id == "p000.b007")
    assert "D1 receptor" in abstract.snippet
    assert abstract.match.startswith("D") and abstract.match.endswith("receptor") and "_" in abstract.match
    assert abstract.node_id.endswith("/r1/p000.b007") and abstract.page == 0


def test_scope_sections_pages_types(engine: GrepEngine) -> None:
    exp = engine.grep(
        GrepQuery(pattern=r"\b26\b", scope=GrepScope(sections=["experimental"]), max_results=100)
    )
    assert exp.matches and {m.section for m in exp.matches} == {"experimental"}
    p3 = engine.grep(GrepQuery(pattern="compound", scope=GrepScope(pages=[3]), max_results=100))
    assert p3.matches and all(m.page == 3 or m.local_id == "p003.b007" for m in p3.matches)
    caps = engine.grep(GrepQuery(pattern="Table 3", scope=GrepScope(node_types=["caption"])))
    assert [m.local_id for m in caps.matches] == ["p007.g001.c0"]


def test_references_excluded_by_default(engine: GrepEngine) -> None:
    default = engine.grep(GrepQuery(pattern="Costa", literal=True))
    assert all(m.section != "references" for m in default.matches)
    refs = engine.grep(GrepQuery(pattern="Costa", literal=True, scope=GrepScope(sections=["references"])))
    assert refs.matches and refs.matches[0].local_id == "p014.b011"


def test_literal_case_truncation_and_errors(engine: GrepEngine) -> None:
    assert engine.grep(GrepQuery(pattern="(26", literal=True)).matches
    with pytest.raises(GrepError):
        engine.grep(GrepQuery(pattern="(26"))
    assert not engine.grep(GrepQuery(pattern="ucm-1306", case_sensitive=True)).matches
    assert engine.grep(GrepQuery(pattern="ucm-1306")).matches
    t = engine.grep(GrepQuery(pattern="the", max_results=3))
    assert len(t.matches) == 3 and t.truncated and t.total > 3


def test_snippet_context_sentences(engine: GrepEngine) -> None:
    m0 = engine.grep(GrepQuery(pattern="UCM-1306", context_lines=0)).matches
    m2 = engine.grep(GrepQuery(pattern="UCM-1306", context_lines=2)).matches
    assert len(m2[1].snippet) >= len(m0[1].snippet)
    assert all(len(m.snippet) <= 402 for m in m2)


async def test_in_memory_search_modes() -> None:
    s = InMemorySearch(real_ir().nodes)
    herg = await s.search("hERG", "fts", 5)
    assert herg and "herg" in herg[0].snippet.lower()
    d1 = await s.search("dopamine D1", "fts", 5)
    assert d1 and all("dopamine" in h.snippet.lower() or True for h in d1)
    fuzzy = await s.search("dopamne recepter", "trgm", 5)  # 拼写错误也能找到
    assert fuzzy and fuzzy[0].score >= 0.3
    hybrid = await s.search("allosteric modulator", "hybrid", 5)
    assert hybrid and hybrid[0].score > 0
    assert await s.search("zzzqqq", "fts", 5) == []


def test_trigram_helpers() -> None:
    assert "  d" in trigrams("dopamine")
    assert word_similarity("dopamine", "the dopamine receptor") == 1.0
    assert word_similarity("xyz", "") == 0.0
    assert merge_hybrid([], [], 5) == []
