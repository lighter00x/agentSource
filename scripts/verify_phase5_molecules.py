"""Real molecular delivery verification. References are never sent to the model."""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from typing import Any

from rdkit import Chem

from drugpaper.agents.figure.graph import FigureAgent
from drugpaper.chem.esmiles import split
from drugpaper.services import Services
from scripts.ingest_phase5_data import ROOT, settings_for


def connectivity(raw: str) -> str | None:
    mol = Chem.MolFromSmiles(split(raw)[0])
    if mol is None:
        return None
    Chem.RemoveStereochemistry(mol)
    return Chem.MolToSmiles(mol)


def compare_figure1(report: dict[str, Any], gold: dict[str, Any]) -> dict[str, Any]:
    errors = []
    coverage = report.get("molecular_coverage", {})
    if coverage.get("detected_count") != gold["expected_count"]:
        errors.append("detected molecule count differs from original image reference")
    bindings = []

    def walk(value: Any) -> None:
        if isinstance(value, dict):
            label = value.get("reference")
            for b in value.get("molecular_bindings", []):
                if label:
                    bindings.append((label, b))
            for key, child in value.items():
                if key != "molecular_bindings":
                    walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)

    walk(report.get("backfilled_extraction", {}))
    for expected in gold["molecules"]:
        matches = [b for label, b in bindings if label == expected["reference"]]
        unique = {b["mol_id"]: b for b in matches}
        if len(unique) != 1:
            errors.append(expected["reference"] + ": missing or ambiguous label binding")
            continue
        b = next(iter(unique.values()))
        rect = b["bbox"]
        region = expected["region"]
        x = (rect["x0"] + rect["x1"]) / 2
        y = (rect["y0"] + rect["y1"]) / 2
        if not (region["x0"] <= x <= region["x1"] and region["y0"] <= y <= region["y1"]):
            errors.append(expected["reference"] + ": bound to wrong image region")
        if connectivity(b["e_smiles_raw"]) != connectivity(expected["connectivity_smiles"]):
            errors.append(expected["reference"] + ": chemical connectivity mismatch")
    return {
        "status": "failed" if errors else "passed",
        "errors": errors,
        "scope": "Figure1 two molecules: count, labels, left/right regions and chemical connectivity; stereochemistry not independently certified",
    }


def compare_sar_molecules(report: dict[str, Any], gold: dict[str, Any]) -> dict[str, Any]:
    sar = report.get("backfilled_extraction", {}).get("sar_records", {})
    rows = sar.get("rows", [])
    errors = []
    if len(rows) != len(gold["molecules"]) or sar.get("status") != "completed":
        errors.append("SAR full molecular assembly is incomplete")
    for i, expected in enumerate(gold["molecules"]):
        if i >= len(rows):
            break
        row = rows[i]
        if row.get("label") != expected["label"] or row.get("values") != expected["values"]:
            errors.append(f"row {i}: wrong compound/assignment binding")
        structures = row.get("structures", [])
        if len(structures) != 1:
            errors.append(f"row {i}: missing or ambiguous complete structure")
        elif connectivity(structures[0]["canonical_smiles"]) != connectivity(expected["smiles"]) or (
            "formula" in expected and structures[0]["formula"] != expected["formula"]
        ):
            errors.append(f"row {i}: structure/formula differs from manual reference")
    return {"status": "failed" if errors else "passed", "errors": errors, "scope": gold["scope"]}


async def verify(root: Path, nodes: list[str], output_name: str = "molecular-delivery") -> dict[str, Any]:
    sv = Services.from_settings(settings_for(root))
    bundle = await sv.skills()
    original_chat = bundle.models.chat
    records = json.loads((root / "ingest.json").read_text())
    doc = next(r["result"]["document_id"] for r in records if r["file"] == "jmedchem_2c00949")
    out = root / output_name
    out.mkdir(exist_ok=True)

    async def observe(req, *, task="default"):
        texts = [m.text() for m in req.messages]
        if task == "figure_structure" and any("e_smiles_raw" in text for text in texts):
            raise AssertionError("Recognized SMILES leaked into structural model input")
        event = {"task": task, "prompt_texts": texts, "status": "received_unverified"}
        try:
            response = await original_chat(req, task=task)
            event["response"] = response.content
            return response
        except Exception as exc:
            event.update(status="failed", error=str(exc))
            if hasattr(exc, "raw"):
                event["raw_response"] = exc.raw
            raise
        finally:
            with (out / "calls.jsonl").open("a") as f:
                f.write(json.dumps(event, ensure_ascii=False) + "\n")

    bundle.models.chat = observe
    result = {"status": "running", "document_id": doc, "figures": []}
    try:
        for node in nodes:
            print("checking", node, flush=True)
            report = await FigureAgent(sv).run(f"{doc}/r1/{node}")
            d = report.model_dump()
            directory = out / node
            directory.mkdir(exist_ok=True)
            (directory / "report.json").write_text(json.dumps(d, ensure_ascii=False, indent=2) + "\n")
            (directory / "before_backfill.json").write_text(
                json.dumps(report.pre_backfill_extraction, ensure_ascii=False, indent=2) + "\n"
            )
            (directory / "after_backfill.json").write_text(
                json.dumps(report.backfilled_extraction, ensure_ascii=False, indent=2) + "\n"
            )
            assets = []
            for product in report.image_products:
                name = f"segment_{product['segment']}_{product['stage']}_page{product.get('page', 0)}.png"
                data = await sv.store_for("default").get(product["blob_ref"])
                if not data.startswith(b"\x89PNG"):
                    raise ValueError("Invalid delivered PNG")
                (directory / name).write_bytes(data)
                assets.append(name)
            comparison = {
                "status": "not_verified",
                "scope": "No independent full molecular reference for this image",
            }
            if node == "p001.b003":
                gold = json.loads(
                    await asyncio.to_thread(
                        Path("tests/fixtures/jmedchem_2c00949/p5_figure1_molecule_gold.json").read_text
                    )
                )
                comparison = compare_figure1(d, gold)
            elif node in {"p003.g000", "p005.g001"}:
                filename = (
                    "p5_table1_molecule_gold.json" if node == "p003.g000" else "p5_table2_molecule_gold.json"
                )
                gold = json.loads(
                    await asyncio.to_thread((Path("tests/fixtures/jmedchem_2c00949") / filename).read_text)
                )
                comparison = compare_sar_molecules(d, gold)
            entry = {
                "node": node,
                "agent_status": report.status,
                "coverage": report.molecular_coverage,
                "independent_comparison": comparison,
                "image_files": assets,
                "report_path": str((directory / "report.json").resolve()),
            }
            result["figures"].append(entry)
            (out / "report.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
            print(
                node,
                report.status,
                comparison["status"],
                report.molecular_coverage.get("resolved_count"),
                flush=True,
            )
    finally:
        await sv.aclose()
    # Even a connectivity reference does not certify stereochemistry or full domain semantics.
    result["status"] = "not_fully_verified"
    (out / "report.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    return result


async def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--root", type=Path, default=ROOT)
    p.add_argument("--nodes", nargs="+", help="Specific nodes; omitted means all figures in the document.")
    p.add_argument("--output-name", default="molecular-delivery")
    args = p.parse_args()
    nodes = args.nodes
    if nodes is None:
        records = json.loads((args.root / "ingest.json").read_text())
        doc = next(r["result"]["document_id"] for r in records if r["file"] == "jmedchem_2c00949")
        baseline = json.loads((args.root / f"{doc}.json").read_text())
        nodes = list(dict.fromkeys(r["node_ref"].split("/")[-1] for r in baseline["reports"]))
    result = await verify(args.root, nodes, args.output_name)
    if result["status"] != "passed":
        raise SystemExit(1)


if __name__ == "__main__":
    asyncio.run(main())
