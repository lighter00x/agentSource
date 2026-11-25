"""S2.11：DbTracer 写 runs 表（SQLite）；PostgreSQL checkpointer 的恢复测试见 tests/integration。"""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import select

from drugpaper.agent.checkpoint import is_postgres_url, open_checkpointer, psycopg_conninfo
from drugpaper.agent.qa import build_ir_qa_agent
from drugpaper.docsrc.ir_source import IrDocumentSource
from drugpaper.persistence.db import Database
from drugpaper.persistence.models import RunRow
from drugpaper.tracing.db import DbTracer
from drugpaper.tracing.jsonl import JsonlTracer, read_events
from tests.fakes.fake_llm import FakeLLM, call, submit, tools
from tests.unit.ir.conftest import needs_real
from tests.unit.ir.real import DOC_ID, real_ir

REF = f"{DOC_ID}/r1/"


@needs_real
async def test_runs_table_has_full_tree(tmp_path: Path) -> None:
    db = Database(f"sqlite+aiosqlite:///{tmp_path / 't.db'}")
    await db.create_all()
    tracer = DbTracer(db, jsonl=JsonlTracer(tmp_path / "runs"))
    llm = FakeLLM(
        [
            tools(call("grep", {"pattern": "UCM-1306"}), call("get_node", {"node_id": "p999.b000"})),
            submit("化合物 26", [{"ref": REF + "p000.b007"}]),
        ]
    )
    agent = build_ir_qa_agent(IrDocumentSource(real_ir()), llm, tracer)
    res = await agent.ask("UCM-1306 是哪个化合物？", run_id="run_db1")
    await tracer.aclose()
    assert not tracer.errors

    async with db.session() as s:
        top = await s.get(RunRow, "run_db1")
        assert top is not None and top.run_type == "agent" and top.component == "doc_qa_ir"
        assert top.status == res.stop_reason == "answered" and top.ended_at is not None
        assert top.metrics["model_calls"] == 2 and top.metrics["tool_calls"] == 2
        assert top.document_id == DOC_ID and top.trace_id
        kids = list((await s.execute(select(RunRow).where(RunRow.parent_run_id == "run_db1"))).scalars())
    types = sorted(k.run_type for k in kids)
    assert types == ["model_call", "model_call", "tool", "tool", "validation"]
    tool_rows = {k.component: k for k in kids if k.run_type == "tool"}
    assert tool_rows["grep"].status == "ok" and tool_rows["get_node"].status == "error"
    assert tool_rows["get_node"].error and "不存在" in tool_rows["get_node"].error["message"]
    mc = next(k for k in kids if k.run_type == "model_call")
    assert "input_tokens" in mc.metrics and mc.model is not None
    assert all(len(str(v)) <= 2000 for k in kids for v in (k.outputs or {}).values())
    # JSONL 同时保留
    assert read_events(tmp_path / "runs" / "run_db1.jsonl")[-1]["kind"] == "run_end"
    await db.dispose()


def test_conninfo_and_memory_fallback() -> None:
    info = psycopg_conninfo("postgresql+asyncpg://u:p@h:5432/db")
    assert info == "postgresql://u:p@h:5432/db?options=-csearch_path%3Dlg"
    assert is_postgres_url("postgresql://h/db") and not is_postgres_url("sqlite+aiosqlite:///x.db")


async def test_open_checkpointer_memory_for_sqlite() -> None:
    from langgraph.checkpoint.memory import InMemorySaver

    async with open_checkpointer("sqlite+aiosqlite:///x.db") as saver:
        assert isinstance(saver, InMemorySaver)
