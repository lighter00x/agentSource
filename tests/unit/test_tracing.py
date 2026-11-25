from __future__ import annotations

import io
from pathlib import Path

from rich.console import Console

from drugpaper.tracing.console import ConsolePrinter
from drugpaper.tracing.jsonl import CompositeTracer, JsonlTracer, read_events, summarize


def test_summarize_truncates() -> None:
    out = summarize({"a": "x" * 500, "b": list(range(60)), "c": {"d": 1.5, "e": None}, "f": object()})
    assert len(out["a"]) == 201 and out["a"].endswith("…")
    assert len(out["b"]) == 51 and out["b"][-1] == "…(+10)"
    assert out["c"] == {"d": 1.5, "e": None}
    assert isinstance(out["f"], str)


def test_jsonl_meta_and_reserved_keys(tmp_path: Path) -> None:
    t = JsonlTracer(tmp_path / "runs")
    t.event("r1", "run_start", {"trace_id": "t1", "parent_run_id": "p0", "agent": "qa"})
    t.event("r1", "tool_call", {"name": "grep", "kind": "should-not-override", "run_id": "x"})
    t.event("r1", "run_end", {"stop_reason": "answered"})
    ev = read_events(t.path_for("r1"))
    assert [e["kind"] for e in ev] == ["run_start", "tool_call", "run_end"]
    assert all(e["trace_id"] == "t1" and e["parent_run_id"] == "p0" and e["run_id"] == "r1" for e in ev)
    assert "ts" in ev[0]


def test_console_printer_and_composite(tmp_path: Path) -> None:
    buf = io.StringIO()
    printer = ConsolePrinter(Console(file=buf, width=200, color_system=None))
    t = CompositeTracer([printer, JsonlTracer(tmp_path)])
    t.event("r", "run_start", {"agent": "qa", "user_input": "问题"})
    t.event("r", "model_call", {"phase": "agent", "ok": True, "tool_calls": ["grep"], "input_tokens": 3})
    t.event("r", "tool_call", {"name": "grep", "args": "{}", "ok": False, "error": "timeout", "refs": 0})
    t.event("r", "validation", {"ok": False, "issues": ["引用无效"], "retry": True})
    t.event("r", "budget_exhausted", {"reason": "steps 8/8"})
    t.event("r", "run_end", {"stop_reason": "answered", "steps": 1})
    out = buf.getvalue()
    for s in [
        "run r",
        "step 1",
        "grep",
        "timeout",
        "validation retry",
        "budget exhausted",
        "stop_reason=answered",
    ]:
        assert s in out
    assert len(read_events(tmp_path / "r.jsonl")) == 6
