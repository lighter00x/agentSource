"""P6 full workflow verification: explicit controlled replay or an existing real document."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import io
import json
import tempfile
import zipfile
from pathlib import Path
from typing import Any

from drugpaper.agents.reader.notes import ReaderNotes
from drugpaper.config.settings import Settings
from drugpaper.evals.silver import evaluate, report_markdown
from drugpaper.exporters.package import create_export
from drugpaper.facts.full_text import load_context
from drugpaper.orchestration.full_text_pipeline import FullTextPipeline
from drugpaper.profiles.projector import ProjectionResult
from drugpaper.services import Services
from drugpaper.validators.projection import validate_projection
from scripts.freeze_phase_6 import freeze


async def inspect_result(
    sv: Services, document_id: str, out: Path, result: dict[str, Any], *, mode: str
) -> dict[str, Any]:
    ctx = await load_context(sv.db, document_id)
    await asyncio.to_thread(out.mkdir, parents=True, exist_ok=True)
    (out / "pipeline-result.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2, default=str) + "\n"
    )
    checks = []
    if result.get("projection"):
        projected = ProjectionResult.model_validate(result["projection"])
        validation = validate_projection(projected, ctx)
        checks.append(
            {
                "name": "V-J01..05 and nonnull primary evidence",
                "ok": not validation,
                "issues": [c.model_dump() for c in validation],
            }
        )
        ids = ctx.by_id()
        proposal = result["proposal"]
        lead, final = ids.get(proposal["lead_compound_id"], {}), ids.get(proposal["final_compound_id"], {})
        checks.append(
            {
                "name": "final label 26",
                "ok": final.get("primary_label") == "26",
                "lead_label": lead.get("primary_label"),
                "final_label": final.get("primary_label"),
            }
        )
        checks.append(
            {"name": "path checks", "ok": not result.get("checks"), "attempts": result.get("attempts")}
        )
        exported = await create_export(sv, document_id, result_id=result["result_id"])
        archive = await sv.store.get(exported["blob_ref"])
        (out / "full-text-export.zip").write_bytes(archive)
        with zipfile.ZipFile(io.BytesIO(archive)) as z:
            manifest = json.loads(z.read("manifest.json"))
            good = all(
                hashlib.sha256(z.read(name)).hexdigest() == info["sha256"]
                for name, info in manifest["files"].items()
            )
            checks.append({"name": "export files and hashes", "ok": good, "files": z.namelist()})
            for name in (
                "result.drug_optimization_v1.json",
                "result.drug_optimization_v1.audit.json",
                "manifest.json",
                "paper.corpus.md",
                "paper.audit.md",
                "measurements.csv",
                "chunks.jsonl",
            ):
                (out / name).write_bytes(z.read(name))
        notes = ReaderNotes.model_validate(result["reader"]["notes"])
        # Reader budget is measured separately from T1/P5; a controlled replay is not a corpus median.
        reading_units = sum(len(pack) for pack in [result["reader"]["notes"]["pages_read"]])
        budget = reading_units + 3 + 2 * len(notes.lookup_answers)
        checks.append(
            {
                "name": "bounded synthesis and reader model budget",
                "ok": result["attempts"] <= 3 and notes.model_calls <= budget,
                "model_calls": notes.model_calls,
                "tool_calls": notes.tool_calls,
                "reader_bound": budget,
                "scope": "reader+synthesis only; not the real-paper median",
            }
        )
    else:
        checks.append(
            {
                "name": "page readiness",
                "ok": False,
                "status": result["status"],
                "pending_refs": result.get("reader", {}).get("pending_refs", []),
            }
        )
    silver = evaluate(Path("tests/fixtures/silver"), Path("samples/outputs/phase-6/silver"))
    (out / "silver_report.json").write_text(json.dumps(silver, ensure_ascii=False, indent=2) + "\n")
    (out / "silver_report.md").write_text(report_markdown(silver))
    report = {
        "stage": "P6",
        "mode": mode,
        "document_id": document_id,
        "status": result["status"],
        "checks": checks,
        "workflow_passed": bool(checks) and all(c["ok"] for c in checks),
        "silver_prediction_coverage": silver["coverage"],
        "real_acceptance_complete": mode == "real_existing_document"
        and result["status"] == "completed"
        and silver["complete"],
        "scope": "Controlled replay verifies implementation only; real fixture OPSIN/HRMS, upstream P5 and ten independent predictions must pass separately.",
    }
    (out / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    return report


async def verify_controlled(out: Path) -> dict[str, Any]:
    from tests.fakes.fake_llm import FakeLLM, text
    from tests.unit.reader.test_full_text import delta, full_env, proposal

    await asyncio.to_thread(out.mkdir, parents=True, exist_ok=True)
    # The fixture is explicitly authored synthetic input, never a silver reference or real prediction.
    data_dir = Path(await asyncio.to_thread(tempfile.mkdtemp, prefix="controlled-data-", dir=out))
    seed = full_env.__wrapped__(data_dir)
    sv, ir = await anext(seed)
    try:
        llm = FakeLLM(
            [
                text(delta(ir, 0).model_dump_json()),
                text(delta(ir, 1).model_dump_json()),
                text(proposal(ir).model_dump_json()),
            ]
        )
        result = await FullTextPipeline(sv, llm).run(ir.document_id, prepare_text=False)
        return await inspect_result(sv, ir.document_id, out, result, mode="controlled_fake_model")
    finally:
        await seed.aclose()


async def verify_real(document_id: str, out: Path) -> dict[str, Any]:
    sv = Services.from_settings(Settings())
    try:
        result = await FullTextPipeline(sv).run(document_id)
        return await inspect_result(sv, document_id, out, result, mode="real_existing_document")
    finally:
        await sv.aclose()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--out", type=Path, default=Path("samples/outputs/phase-6/20261005-full-text/workflow")
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--controlled", action="store_true", help="明确的合成数据+FakeLLM完整链路，不计真实内容验收"
    )
    group.add_argument("--document", help="在配置的数据库上运行指定真实文档，需已有P5报告")
    args = parser.parse_args()
    freeze()
    report = asyncio.run(
        verify_controlled(args.out.resolve())
        if args.controlled
        else verify_real(args.document, args.out.resolve())
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["workflow_passed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
