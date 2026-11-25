"""P5 isolated live figure/corpus verification; no business facts are overwritten."""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import time
from pathlib import Path

from sqlalchemy import select

from drugpaper.orchestration.figure_pipeline import FigurePipeline
from drugpaper.persistence.models import MeasurementRow
from drugpaper.services import Services
from scripts.ingest_phase5_data import ROOT, settings_for


def validate_table3(measurements: list[dict]) -> dict:
    expected = {
        "solubility": (50, "μM"),
        "ppb": (79, "%"),
        "pampa_pe": (24e-6, "cm/s"),
        "herg": (21, "%"),
        "serum_stability": (93, "%"),
        "liver_homogenate_stability": (65, "%"),
        "cyp2d6_inhibition": (3, "%"),
        "cyp3a4_inhibition": (9, "%"),
        "cyp1a2_inhibition": (49, "%"),
        "cyp2c9_inhibition": (38, "%"),
        "cyp2c19_inhibition": (37, "%"),
    }
    errors = []
    for metric, (value, unit) in expected.items():
        candidates = [m for m in measurements if m.get("metric") == metric]
        if len(candidates) != 1:
            errors.append(f"{metric}: expected one active measurement, got {len(candidates)}")
            continue
        m = candidates[0]
        observed = m.get("value")
        if (
            not isinstance(observed, int | float)
            or isinstance(observed, bool)
            or not math.isclose(observed, value, rel_tol=1e-6, abs_tol=1e-12)
        ):
            errors.append(f"{metric}: value mismatch")
        expected_error = {
            "cyp2d6_inhibition": 1,
            "cyp3a4_inhibition": 1,
            "cyp1a2_inhibition": 2,
            "cyp2c9_inhibition": 3,
            "cyp2c19_inhibition": 2,
        }.get(metric)
        if m.get("error") != expected_error:
            errors.append(f"{metric}: uncertainty mismatch")
        if m.get("qualifier") != "=":
            errors.append(f"{metric}: qualifier mismatch")
        if m.get("unit") != unit or m.get("subject_label") != "26":
            errors.append(f"{metric}: unit or subject mismatch")
    kd = [m for m in measurements if m.get("metric") == "kd"]
    if (
        len(kd) != 1
        or kd[0].get("unit") != "M"
        or kd[0].get("subject_label") != "26"
        or not isinstance(kd[0].get("value"), int | float)
        or not math.isclose(kd[0]["value"], 1.56e-4, rel_tol=1e-6)
    ):
        errors.append("kd: secondary binding value, unit or subject mismatch")
    return {
        "status": "passed" if not errors else "needs_review",
        "primary_count": len(expected),
        "errors": errors,
    }


async def verify(root: Path, *, fixture_only: bool = False, document_concurrency: int = 1) -> dict:
    from scripts.audit_phase5_results import audit_figure, compare_table1

    settings = settings_for(root)
    sv = Services.from_settings(settings)
    records = json.loads((root / "ingest.json").read_text())
    records = [
        r
        for r in records
        if r["result"]["status"] == "ready" and (not fixture_only or r["file"] == "jmedchem_2c00949")
    ]
    result = {"documents": [], "status": "running"}
    gate = asyncio.Semaphore(document_concurrency)

    async def one(record: dict) -> None:
        async with gate:
            doc = record["result"]["document_id"]
            started = time.perf_counter()
            print("figures", record["file"], flush=True)
            try:
                data = await FigurePipeline(sv).run(doc)
                entry = {
                    "file": record["file"],
                    "document_id": doc,
                    "status": data["status"],
                    "elapsed_s": round(time.perf_counter() - started, 2),
                    "calls": data["calls"],
                    "reports": len(data["reports"]),
                    "figure_statuses": {
                        s: sum(r["status"] == s for r in data["reports"])
                        for s in ["ok", "remediated", "needs_review", "failed"]
                    },
                    "content_checks": [audit_figure(r) for r in data["reports"]],
                }
                entry["content_status"] = (
                    "not_passed"
                    if any(c["machine_check"] != "passed" for c in entry["content_checks"])
                    else "not_verified"
                )
                (root / f"{doc}.json").write_text(json.dumps(data, ensure_ascii=False, indent=2, default=str))
                if record["file"] == "jmedchem_2c00949":
                    async with sv.db.session() as session:
                        measurements = list(
                            (
                                await session.execute(
                                    select(MeasurementRow).where(
                                        MeasurementRow.document_id == doc, MeasurementRow.is_active.is_(True)
                                    )
                                )
                            ).scalars()
                        )
                    entry["table3_metrics"] = [m.payload for m in measurements if "/p007." in m.target_ref]
                    entry["table3_validation"] = validate_table3(entry["table3_metrics"])
                    entry["table1_sar"] = [
                        r["extraction"] for r in data["reports"] if r["image_type"] == "sar_table"
                    ]
                    gold_path = Path("tests/fixtures/jmedchem_2c00949/p5_table1_gold.json")
                    gold = json.loads(await asyncio.to_thread(gold_path.read_text))
                    table1 = next((r for r in data["reports"] if r["node_ref"].endswith("/p003.g000")), {})
                    entry["table1_validation"] = compare_table1(table1.get("extraction") or {}, gold)
            except Exception as exc:
                entry = {
                    "file": record["file"],
                    "document_id": doc,
                    "status": "failed",
                    "error": f"{type(exc).__name__}: {exc}",
                }
            result["documents"].append(entry)
            (root / ("fixture-report.json" if fixture_only else "report.json")).write_text(
                json.dumps(result, ensure_ascii=False, indent=2, default=str)
            )
            print(record["file"], entry["status"], entry.get("figure_statuses", {}), flush=True)

    try:
        await asyncio.gather(*(one(record) for record in records))
    finally:
        await sv.aclose()
    corpus = [d for d in result["documents"] if d["file"] != "jmedchem_2c00949"]
    result["corpus_count"] = len(corpus)
    result["success_rate"] = sum(d["status"] == "completed" for d in corpus) / len(corpus) if corpus else None
    result["processing_status"] = (
        "passed"
        if (fixture_only and all(d["status"] == "completed" for d in result["documents"]))
        or (not fixture_only and len(corpus) == 10 and result["success_rate"] >= 0.9)
        else "needs_review"
    )
    fixture = next((d for d in result["documents"] if d["file"] == "jmedchem_2c00949"), {})
    sar_complete = fixture.get("table1_validation", {}).get("status") == "passed"
    result["content_status"] = (
        "not_passed"
        if any(d.get("content_status") != "not_verified" for d in result["documents"])
        else "not_verified"
    )
    result["status"] = (
        "passed"
        if result["processing_status"] == "passed"
        and fixture.get("status") == "completed"
        and fixture.get("table3_validation", {}).get("status") == "passed"
        and sar_complete
        and all(d.get("content_status") == "passed" for d in result["documents"])
        else "not_passed"
    )
    (root / ("fixture-report.json" if fixture_only else "report.json")).write_text(
        json.dumps(result, ensure_ascii=False, indent=2, default=str)
    )
    return result


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=ROOT)
    parser.add_argument("--fixture-only", action="store_true")
    parser.add_argument("--document-concurrency", type=int, choices=range(1, 5), default=1)
    args = parser.parse_args()
    result = await verify(
        args.output, fixture_only=args.fixture_only, document_concurrency=args.document_concurrency
    )
    print(
        json.dumps(
            {
                "status": result["status"],
                "corpus_count": result["corpus_count"],
                "success_rate": result["success_rate"],
            },
            ensure_ascii=False,
        )
    )
    if result["status"] != "passed":
        raise SystemExit(1)


if __name__ == "__main__":
    asyncio.run(main())
