"""One authorized real-VLM closed check of a deliberately changed printed value."""

import asyncio
import copy
import json

from drugpaper.services import Services
from drugpaper.tools.protocol import ToolContext
from scripts.ingest_phase5_data import ROOT, settings_for


async def main():
    records = json.loads((ROOT / "ingest.json").read_text())
    doc = next(r["result"]["document_id"] for r in records if r["file"] == "jmedchem_2c00949")
    data = json.loads((ROOT / f"{doc}.json").read_text())
    report = next(r for r in data["reports"] if r["node_ref"].endswith("/p007.g001"))
    extraction = copy.deepcopy(report["extraction"])
    basis = "live_extraction"
    if not any(len(row) > 1 and row[1].get("text") == "50" for row in extraction.get("rows", [])):
        from tests.unit.figure.test_values_and_tables import table3

        extraction = table3().model_dump()
        basis = "manual_fixture"
    row = next(i for i, r in enumerate(extraction["rows"]) if r[1]["text"] == "50")
    path = f"rows[{row}][1].text"
    extraction["rows"][row][1]["text"] = "500"
    sv = Services.from_settings(settings_for())
    try:
        bundle = await sv.skills()
        ctx = await bundle.context_for(
            ToolContext(
                user_id="default", document_id=doc, run_id="p5_live_selfcheck_demo", trace_id="p5_demo"
            ),
            report["node_ref"],
        )
        result = await bundle.runtime.run(
            "figure_self_check",
            report["node_ref"],
            {
                "checklist": [{"dimension": "numeric", "field_path": path, "expected": "500"}],
                "extraction": extraction,
                "focus": "Verify the indicated cell against the original printed figure, reporting a suggested_value if incorrect.",
            },
            ctx,
            force=True,
        )
        payload = result.artifacts[0].payload if result.artifacts else {}
        item = next((i for i in payload.get("items", []) if i["field_path"] == path), {})
        demo = {
            "status": "passed"
            if item.get("verdict") == "incorrect" and str(item.get("suggested_value")) in {"50", "50.0"}
            else "needs_review",
            "run_id": result.run_id,
            "basis": basis,
            "changed_from": "50",
            "changed_to": "500",
            "field_path": path,
            "review": payload,
            "error": result.error,
        }
        (ROOT / "selfcheck-demo.json").write_text(json.dumps(demo, ensure_ascii=False, indent=2))
        print(demo["status"], item)
    finally:
        await sv.aclose()


if __name__ == "__main__":
    asyncio.run(main())
