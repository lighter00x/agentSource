"""Reexecute production molecular Skills on an unchanged saved ID-only visual extraction."""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from rdkit import Chem

from drugpaper.services import Services
from drugpaper.tools.protocol import ToolContext
from scripts.ingest_phase5_data import ROOT, settings_for


async def recheck(source: Path, output: Path) -> None:
    report = json.loads(await asyncio.to_thread(source.read_text))
    source_path = await asyncio.to_thread(source.resolve)
    output_path = await asyncio.to_thread(output.resolve)
    extraction = report["pre_backfill_extraction"]
    sv = Services.from_settings(settings_for(ROOT))
    try:
        bundle = await sv.skills()
        ctx = await bundle.context_for(
            ToolContext(
                user_id="default",
                document_id=report["document_id"],
                run_id="real-instance-recheck",
                trace_id="real-instance-recheck",
            ),
            report["node_ref"],
        )
        result = await bundle.runtime.run(
            "mol_backfill",
            report["node_ref"],
            {"contract": "universal", "extraction_payload": extraction},
            ctx,
            force=True,
        )
        if result.status != "completed":
            raise RuntimeError(result.error)
        delivery = result.artifacts[0].payload
        payload = delivery["payload"]
        ctx = await bundle.context_for(
            ToolContext(
                user_id="default",
                document_id=report["document_id"],
                run_id="real-route-recheck",
                trace_id="real-route-recheck",
            ),
            report["node_ref"],
        )
        routes = await bundle.runtime.run(
            "scheme_relations", report["node_ref"], {"extraction": payload}, ctx, force=True
        )
        if routes.status != "completed":
            raise RuntimeError(routes.error)
        relation_data = routes.artifacts[0].payload
        payload["relations"] = relation_data["relations"]
        payload["route_errors"] = relation_data["errors"]
        checks = []
        for instance in payload.get("compound_instances", []):
            record = {"reference": instance["reference"], "status": instance.get("assembly_status")}
            if instance["reference"].startswith(("(R)", "(S)")):
                raw = instance.get("assembled_molecule", {}).get("canonical_smiles")
                centers = (
                    Chem.FindMolChiralCenters(
                        Chem.MolFromSmiles(raw), includeUnassigned=True, useLegacyImplementation=False
                    )
                    if raw
                    else []
                )
                record["cip_matches_label"] = len(centers) == 1 and centers[0][1] == instance["reference"][1]
                if not record["cip_matches_label"]:
                    raise ValueError(f"Final CIP mismatch: {instance['reference']}")
            checks.append(record)
        await asyncio.to_thread(output.mkdir, parents=True, exist_ok=False)
        await asyncio.to_thread(
            (output / "pre_backfill_extraction.json").write_text,
            json.dumps(extraction, ensure_ascii=False, indent=2) + "\n",
        )
        await asyncio.to_thread(
            (output / "backfilled_extraction.json").write_text,
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        )
        evidence = {
            "status": "partial_delivery_needs_review",
            "source_report": str(source_path),
            "method": "Unchanged saved visual extraction -> production owned Skill Runtime -> chemistry -> fact persistence",
            "molecular_errors": delivery["errors"],
            "coverage": delivery["coverage"],
            "instance_checks": checks,
            "route_errors": relation_data["errors"],
            "scope": "CIP and programmatic structure consistency only; independent whole-route visual accuracy not certified",
        }
        await asyncio.to_thread(
            (output / "report.json").write_text, json.dumps(evidence, ensure_ascii=False, indent=2) + "\n"
        )
        print(
            json.dumps(
                {
                    "molecular_errors": delivery["errors"],
                    "instance_count": len(checks),
                    "all_explicit_cip_matches": all(c.get("cip_matches_label", True) for c in checks),
                    "route_error_count": len(relation_data["errors"]),
                    "output": str(output_path),
                },
                ensure_ascii=False,
            )
        )
    finally:
        await sv.aclose()


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    await recheck(args.source, args.output)


if __name__ == "__main__":
    asyncio.run(main())
