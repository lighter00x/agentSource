"""Ten literal questions on the real sample IR, using an isolated verification DB."""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from sqlalchemy import select

from drugpaper.agents.lookup.batch import DocLookup
from drugpaper.agents.lookup.models import LookupQuestion
from drugpaper.config.settings import Settings
from drugpaper.facts.full_text import load_context
from drugpaper.persistence.models import Document
from drugpaper.services import Services


async def verify(database: Path, output: Path) -> bool:
    if not await asyncio.to_thread(database.is_file):
        raise ValueError("Run verify_phase_6 in this isolated directory first")
    golden = json.loads(await asyncio.to_thread(Path("tests/fixtures/p6_lookup_sample.json").read_text))
    resolved_database = await asyncio.to_thread(database.resolve)
    services = Services.from_settings(
        Settings(
            database_url=f"sqlite+aiosqlite:///{resolved_database}",
            blob_root=database.parent / "blobs",
            redis_url="",
            skills_watch_enabled=False,
        )
    )
    try:
        async with services.db.session() as session:
            document = (await session.execute(select(Document))).scalars().one()
            document_id = document.id
        ctx = await load_context(services.db, document_id)
        lookup = DocLookup(services)
        rows, batches = [], []
        for start in range(0, len(golden["questions"]), 8):
            source = golden["questions"][start : start + 8]
            questions = [LookupQuestion(question=g["question"]) for g in source]
            batch = await lookup.lookup(questions, ctx)
            batches.append(batch.model_dump(mode="json"))
            for expected, actual in zip(source, batch.results, strict=True):
                ok = actual.status == expected["expected_status"]
                if "expected_answer" in expected:
                    ok = ok and actual.answer == expected["expected_answer"]
                    ok = ok and any(
                        e.node_id == f"{document_id}/r{ctx.ir.revision}/{expected['expected_local_id']}"
                        for e in actual.evidence
                    )
                if "expected_pages" in expected:
                    ok = ok and actual.suggested_pages == expected["expected_pages"]
                rows.append({"expected": expected, "actual": actual.model_dump(mode="json"), "ok": ok})
        repeat = await lookup.lookup(
            [LookupQuestion(question=g["question"]) for g in golden["questions"][:8]], ctx
        )
        report = {
            "scope": golden["scope"],
            "document_id": document_id,
            "fingerprint": ctx.fingerprint,
            "questions": len(rows),
            "correct": sum(r["ok"] for r in rows),
            "accuracy": sum(r["ok"] for r in rows) / len(rows),
            "model_calls": sum(b["model_calls"] for b in batches) + repeat.model_calls,
            "repeat_cache_hits": repeat.cache_hits,
            "passed": all(r["ok"] for r in rows) and repeat.cache_hits == 8,
            "rows": rows,
            "batches": batches,
        }
        await asyncio.to_thread(output.parent.mkdir, parents=True, exist_ok=True)
        await asyncio.to_thread(output.write_text, json.dumps(report, ensure_ascii=False, indent=2) + "\n")
        print(json.dumps({k: v for k, v in report.items() if k not in {"rows", "batches"}}))
        return report["passed"]
    finally:
        await services.aclose()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    raise SystemExit(0 if asyncio.run(verify(args.database, args.output)) else 1)


if __name__ == "__main__":
    main()
