"""Audit persisted P5 results without model calls; model verdicts are not ground truth."""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any

from drugpaper.docsrc.textnorm import strip_latex
from drugpaper.facts.figure_models import ChartDataset, TableGrid
from drugpaper.facts.table_processing import expand_rows, map_measurements
from scripts.verify_phase_5 import validate_table3

ROOT = Path("samples/outputs/phase-5/20261002")


def normalized(text: str) -> str:
    # Math delimiters and ionic-charge superscripts are typography, not content.
    # Retain numeric exponents, signs and units so scientific differences still fail.
    text = re.sub(r"\$([^$]*)\$", r"\1", text)
    text = re.sub(r"\^\s*\{?([+-])\}?", r"\1", text)
    text = strip_latex(text).replace("′", "'").replace("’", "'").replace("µ", "μ").replace("–", "-")
    return re.sub(r"\s+", "", text).lower()


def compare_table_metadata(extraction: dict[str, Any], gold: dict[str, Any]) -> dict[str, Any]:
    errors = []
    try:
        grid = TableGrid.model_validate(extraction)
        for marker, text in gold["footnote_markers"].items():
            observed = grid.footnote_markers.get(marker, "")
            if (
                observed.startswith(marker)
                and len(observed) > 1
                and (observed[1].isupper() or observed[1].isspace())
            ):
                observed = observed[1:].lstrip()
            if normalized(observed).rstrip(".") != normalized(text).rstrip("."):
                errors.append(f"footnote {marker}: missing or different from printed reference")
        for column, markers in gold.get("header_markers", {}).items():
            if (
                not grid.header_rows
                or int(column) >= len(grid.header_rows[0])
                or not set(markers) <= set(grid.header_rows[0][int(column)].markers)
            ):
                errors.append(f"header column {column}: missing footnote association {markers}")
        for row, marker in enumerate(gold.get("row_markers", [])):
            if row >= len(grid.rows) or not grid.rows[row] or marker not in grid.rows[row][0].markers:
                errors.append(f"row {row}: missing footnote association {marker}")
    except (ValueError, TypeError, KeyError) as exc:
        errors.append(f"invalid metadata: {exc}")
    return {
        "status": "failed" if errors else "passed",
        "scope": "Printed footnotes and their cell associations",
        "errors": errors,
    }


def compare_table1(extraction: dict[str, Any], gold: dict[str, Any]) -> dict[str, Any]:
    errors = []
    try:
        grid = TableGrid.model_validate(extraction)
        rows = expand_rows(grid.rows)
        if len(rows) != len(gold["rows"]):
            errors.append(f"row count: {len(rows)} != {len(gold['rows'])}")
        matched = 0
        for r, expected in enumerate(gold["rows"]):
            actual = rows[r] if r < len(rows) else []
            if len(actual) != len(expected):
                errors.append(f"row {r + 1}: expected {len(expected)} columns, got {len(actual)}")
            for c, value in enumerate(expected):
                observed = actual[c].text if c < len(actual) else None
                if observed is not None and normalized(observed) == normalized(value):
                    matched += 1
                else:
                    errors.append(f"rows[{r}][{c}]: expected {value!r}, got {observed!r}")
        total = sum(len(r) for r in gold["rows"])
        return {
            "status": "passed" if not errors else "failed",
            "scope": "Table1 printed data cells",
            "matched_cells": matched,
            "total_cells": total,
            "cell_accuracy": matched / total,
            "errors": errors,
            "reference": "tests/fixtures/jmedchem_2c00949/p5_table1_gold.json",
        }
    except (ValueError, TypeError, KeyError) as exc:
        return {"status": "failed", "errors": [f"invalid grid: {exc}"]}


def audit_figure(report: dict[str, Any]) -> dict[str, Any]:
    reasons = []
    data = report.get("extraction") or {}
    kind = report.get("image_type", "")
    if report.get("status") not in {"ok", "remediated"}:
        reasons.append("agent status is not a passing verdict")
    if not data:
        reasons.append("empty extraction / error fallback")
    elif data.get("status") in {"unclear", "not_a_table", "failed"}:
        reasons.append(f"extraction status: {data['status']}")
    try:
        if kind in {"sar_table", "data_table", "table"}:
            grid = TableGrid.model_validate(data)
            if not grid.rows or not any(c.text or c.mol_id is not None for row in grid.rows for c in row):
                reasons.append("no transcribed table cells")
        elif kind in {"data_chart", "property_plot"}:
            chart = ChartDataset.model_validate(data)
            if not chart.series or not any(s.points_px or s.points for s in chart.series):
                reasons.append("no chart data points")
    except (ValueError, TypeError, AttributeError) as exc:
        reasons.append(f"extraction schema invalid: {str(exc)[:300]}")
    if report.get("open_questions"):
        reasons.append("unresolved open questions")
    if report.get("checks"):
        reasons.append("unresolved L1 checks")
    if report.get("unchecked") or report.get("review", {}).get("unchecked"):
        reasons.append("review coverage incomplete")
    items = report.get("review", {}).get("items", [])
    if any(i.get("verdict") != "correct" for i in items):
        reasons.append("L2 contains incorrect/unsure")
    failed_steps = [t.get("step") for t in report.get("trace", []) if t.get("status") == "failed"]
    if failed_steps:
        reasons.append("failed process steps: " + ", ".join(str(s) for s in failed_steps))
    return {
        "node_ref": report["node_ref"],
        "image_type": kind,
        "agent_status": report.get("status"),
        "machine_check": "failed" if reasons else "passed",
        "reasons": reasons,
        "reference_check": {"status": "not_verified", "reason": "No independent reference comparison"},
        "acceptance": "failed" if reasons else "not_verified",
    }


def audit(root: Path) -> dict[str, Any]:
    batch = json.loads((root / "report.json").read_text())
    gold = json.loads(Path("tests/fixtures/jmedchem_2c00949/p5_table1_gold.json").read_text())
    table3_gold = json.loads(Path("tests/fixtures/jmedchem_2c00949/p5_table3_gold.json").read_text())
    documents = []
    counts: Counter[str] = Counter()
    for entry in batch["documents"]:
        path = root / (entry["document_id"] + ".json")
        payload = json.loads(path.read_text()) if path.exists() else {}
        figures = []
        for report in payload.get("reports", []):
            item = audit_figure(report)
            if entry["file"] == "jmedchem_2c00949":
                if report["node_ref"].endswith("/p003.g000"):
                    item["reference_check"] = compare_table1(report.get("extraction") or {}, gold)
                    item["metadata_comparison"] = compare_table_metadata(report.get("extraction") or {}, gold)
                elif report["node_ref"].endswith("/p007.g001"):
                    item["metadata_comparison"] = compare_table_metadata(
                        report.get("extraction") or {}, table3_gold
                    )
                    try:
                        grid = TableGrid.model_validate(report.get("extraction") or {})
                        measurements, _, _ = map_measurements(grid, entry["document_id"], report["node_ref"])
                        check = validate_table3([m.model_dump() for m in measurements])
                        item["reference_check"] = {
                            **check,
                            "scope": "11 primary values, uncertainties, units, qualifiers, subject and secondary Kd; not full assay validation",
                        }
                    except (ValueError, TypeError) as exc:
                        item["reference_check"] = {"status": "failed", "errors": [str(exc)]}
            if (
                item["reference_check"]["status"] in {"failed", "needs_review"}
                or item.get("metadata_comparison", {}).get("status") == "failed"
            ):
                item["acceptance"] = "failed"
            # A limited reference check does not establish correctness of the whole figure.
            counts[item["acceptance"]] += 1
            figures.append(item)
        documents.append(
            {
                "file": entry["file"],
                "document_id": entry["document_id"],
                "source_report": str(path.resolve()),
                "processing_status": entry["status"],
                "acceptance": "not_passed",
                "figures": figures,
            }
        )
    return {
        "status": "not_passed",
        "definition": "Only independent content comparison can establish correctness. Process output and model self-review are insufficient.",
        "corpus_documents": sum(d["file"] != "jmedchem_2c00949" for d in documents),
        "processing_success_rate": batch.get("success_rate"),
        "figure_acceptance_counts": dict(counts),
        "documents": documents,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    args = parser.parse_args()
    result = audit(args.root)
    (args.root / "content-audit.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    lines = [
        "# P5 内容验收审计",
        "",
        "结论：真实文献整体未通过。单元测试结果不能替代内容验收。",
        "",
        f"十篇整篇处理完成率：{result['processing_success_rate']}；这不是内容正确率。",
        "",
        "| 文献 | 图表数 | 自动检查不通过 | 未独立核验 |",
        "|---|---:|---:|---:|",
    ]
    for d in result["documents"]:
        c = Counter(f["acceptance"] for f in d["figures"])
        lines.append(f"| {d['file']} | {len(d['figures'])} | {c['failed']} | {c['not_verified']} |")
    lines += [
        "",
        "逐图失败理由与有限字段对照结果见 content-audit.json。没有参考标注的结果保持not_verified，不计为通过。",
    ]
    (args.root / "content-audit.md").write_text("\n".join(lines) + "\n")
    print(
        json.dumps(
            {k: result[k] for k in ["status", "processing_success_rate", "figure_acceptance_counts"]},
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
