"""Targeted real table rerun, with independent comparison and preserved raw reports."""

from __future__ import annotations

import argparse
import asyncio
import json
import time
from pathlib import Path

from drugpaper.agents.figure.graph import FigureAgent
from drugpaper.facts.figure_models import TableGrid
from drugpaper.facts.table_processing import map_measurements, sar_rows
from drugpaper.services import Services
from scripts.audit_phase5_results import audit_figure, compare_table1, compare_table_metadata
from scripts.ingest_phase5_data import ROOT, settings_for
from scripts.verify_phase_5 import validate_table3


async def verify(root: Path, nodes: list[str], output_name: str = "table-recheck.json") -> dict:
    records = json.loads((root / "ingest.json").read_text())
    doc = next(r["result"]["document_id"] for r in records if r["file"] == "jmedchem_2c00949")
    sv = Services.from_settings(settings_for(root))
    gold_path = Path("tests/fixtures/jmedchem_2c00949/p5_table1_gold.json")
    gold = json.loads(await asyncio.to_thread(gold_path.read_text))
    table3_gold_path = Path("tests/fixtures/jmedchem_2c00949/p5_table3_gold.json")
    table3_gold = json.loads(await asyncio.to_thread(table3_gold_path.read_text))
    result = {"status": "running", "document_id": doc, "tables": []}
    bundle = await sv.skills()
    original_chat = bundle.models.chat

    async def observed_chat(req, *, task="default"):
        started = time.perf_counter()
        event = {
            "status": "cancelled",
            "task": task,
            "schema": req.response_schema.model_json_schema() if req.response_schema else None,
            "prompt_texts": [m.text() for m in req.messages],
        }
        print("model start", task, flush=True)
        with (root / (output_name + ".calls.jsonl")).open("a") as file:
            file.write(json.dumps({**event, "status": "started"}, ensure_ascii=False) + "\n")
        try:
            response = await original_chat(req, task=task)
            event.update(status="received_unverified", raw_response=response.content)
            return response
        except Exception as exc:
            event.update(status="failed", error=str(exc), raw_response=getattr(exc, "raw", None))
            raise
        finally:
            event["elapsed_s"] = round(time.perf_counter() - started, 3)
            with (root / (output_name + ".calls.jsonl")).open("a") as file:
                file.write(json.dumps(event, ensure_ascii=False) + "\n")
            print("model finish", task, event["status"], event["elapsed_s"], flush=True)

    bundle.models.chat = observed_chat
    try:
        for node in nodes:
            print("checking", node, flush=True)
            report = await FigureAgent(sv).run(f"{doc}/r1/{node}")
            payload = report.model_dump()
            machine = audit_figure(payload)
            comparison = {"status": "not_verified", "reason": "No manual cell reference for this table"}
            if node == "p003.g000":
                comparison = compare_table1(report.extraction, gold)
            elif node == "p007.g001":
                grid = TableGrid.model_validate(report.extraction)
                measurements, assays, _ = map_measurements(grid, doc, report.node_ref)
                comparison = {
                    **validate_table3([m.model_dump() for m in measurements]),
                    "scope": "11 primary values, uncertainties, units, qualifiers, subject and secondary Kd",
                }
                assay_errors = []
                expected_conditions = {
                    "solubility": {"method": "nephelometry"},
                    "ppb": {"compound_conc": "5 μM"},
                    "pampa_pe": {"method": "PAMPA"},
                    "herg": {"compound_conc": "10 μM"},
                    "serum_stability": {"time": ["4 h"]},
                    "liver_homogenate_stability": {"time": ["24 h"]},
                    **{
                        metric: {"compound_conc": "10 μM", "method": "fluorescence"}
                        for metric in (
                            "cyp2d6_inhibition",
                            "cyp3a4_inhibition",
                            "cyp1a2_inhibition",
                            "cyp2c9_inhibition",
                            "cyp2c19_inhibition",
                        )
                    },
                }
                for metric, expected in expected_conditions.items():
                    candidates = [a for a in assays if a.assay_type == metric]
                    if len(candidates) != 1:
                        assay_errors.append(f"{metric}: expected one assay")
                        continue
                    for key, value in expected.items():
                        if candidates[0].conditions.get(key) != value:
                            assay_errors.append(f"{metric}.{key}: condition mismatch")
                    if candidates[0].conditions.get("error_type"):
                        assay_errors.append(f"{metric}: Table3 does not specify SEM/SD")
                comparison["assay_conditions"] = {
                    "status": "failed" if assay_errors else "passed",
                    "scope": "Printed concentrations, methods and respectively ordered times",
                    "errors": assay_errors,
                }
                if assay_errors:
                    comparison["status"] = "failed"
            entry = {
                "node": node,
                "report": payload,
                "machine_check": machine,
                "independent_comparison": comparison,
            }
            if node in {"p003.g000", "p007.g001"}:
                entry["metadata_comparison"] = compare_table_metadata(
                    report.extraction, gold if node == "p003.g000" else table3_gold
                )
            if node == "p003.g000" and report.extraction.get("rows"):
                entry["sar_rows"] = sar_rows(TableGrid.model_validate(report.extraction))
            result["tables"].append(entry)
            (root / output_name).write_text(json.dumps(result, ensure_ascii=False, indent=2))
            print(node, report.status, comparison["status"], comparison.get("matched_cells"), flush=True)
    finally:
        await sv.aclose()
    result["status"] = (
        "passed"
        if result["tables"]
        and all(
            e["machine_check"]["machine_check"] == "passed"
            and e["independent_comparison"]["status"] == "passed"
            and e.get("metadata_comparison", {}).get("status") == "passed"
            for e in result["tables"]
        )
        else "not_passed"
    )
    (root / output_name).write_text(json.dumps(result, ensure_ascii=False, indent=2))
    return result


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--nodes", nargs="+", default=["p003.g000", "p005.g001", "p007.g001"])
    parser.add_argument("--output-name", default="table-recheck.json")
    args = parser.parse_args()
    result = await verify(args.root, args.nodes, args.output_name)
    print("overall", result["status"])
    if result["status"] != "passed":
        raise SystemExit(1)


if __name__ == "__main__":
    asyncio.run(main())
