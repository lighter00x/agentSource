from __future__ import annotations

from drugpaper.agent.state import AgentAnswer, Citation, initial_state
from drugpaper.agent.validators import CitationValidator, normalize_for_match
from drugpaper.docsrc.layout_json import LayoutJsonSource
from tests.conftest import MINI_LAYOUT


def state_with(refs: list[str]):  # noqa: ANN201
    s = initial_state("r", "q", {})
    s["seen_refs"] = refs
    return s


V = CitationValidator(LayoutJsonSource.from_file(MINI_LAYOUT))


def test_valid_citation() -> None:
    ans = AgentAnswer(
        answer="UCM-1306 是化合物 26。",
        citations=[Citation(ref="p000.b007", quote="identification of  UCM-1306 (26)")],  # 大小写/空白归一
    )
    assert V.validate(ans, state_with(["p000.b007"])) == []


def test_unseen_ref() -> None:
    ans = AgentAnswer(answer="x", citations=[Citation(ref="p003.b099")])
    assert V.validate(ans, state_with(["p000.b007"])) == ["引用 p003.b099 未出现在工具结果中"]


def test_quote_mismatch() -> None:
    ans = AgentAnswer(answer="x", citations=[Citation(ref="p000.b007", quote="compound 99 is best")])
    issues = V.validate(ans, state_with(["p000.b007"]))
    assert len(issues) == 1 and "quote" in issues[0]


def test_no_citation() -> None:
    issues = V.validate(AgentAnswer(answer="化合物 26"), state_with([]))
    assert len(issues) == 1 and "没有任何引用" in issues[0]
    assert V.validate(AgentAnswer(answer="  "), state_with([])) == []


def test_sanitize_drops_invalid() -> None:
    ans = AgentAnswer(answer="x", citations=[Citation(ref="p000.b007"), Citation(ref="p003.b099")])
    cleaned = V.sanitize(ans, state_with(["p000.b007"]))
    assert isinstance(cleaned, AgentAnswer)
    assert [c.ref for c in cleaned.citations] == ["p000.b007"]


def test_normalize() -> None:
    assert normalize_for_match("  A\n  B ") == "a b"
