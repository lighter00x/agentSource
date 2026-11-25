"""P6 验证入口：基础重放；--full增加显式受控全文链路，真实内容验收另计。"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from drugpaper.capabilities.tokens import estimate_tokens  # noqa: E402
from drugpaper.config.settings import Settings  # noqa: E402
from drugpaper.orchestration.pipeline_graph import DocumentPipeline  # noqa: E402
from drugpaper.orchestration.text_pipeline import TextPipeline  # noqa: E402
from drugpaper.persistence.repos.nodes import load_ir  # noqa: E402
from drugpaper.services import Services  # noqa: E402
from drugpaper.tools.protocol import ToolContext  # noqa: E402
from tests.fakes.fake_llm import FakeLLM, text  # noqa: E402


async def verify(out: Path, *, live: bool = False, opsin_url: str = "") -> bool:
    await asyncio.to_thread(out.mkdir, parents=True, exist_ok=True)
    settings = Settings(
        database_url=f"sqlite+aiosqlite:///{out / 'foundation.db'}",
        blob_root=out / "blobs",
        skills_root=out / "skills",
        redis_url="",
        mineru_url="",
        skills_watch_enabled=False,
        opsin_base_url=opsin_url,
    )
    services = Services.from_settings(settings)
    original = None
    checks = []
    try:
        await services.db.create_all()
        imported = await DocumentPipeline(services, render_pages=False).run(
            offline_dir=ROOT / "tests/fixtures/jmedchem_2c00949"
        )
        if imported.status != "ready":
            raise RuntimeError(f"sample import failed: {imported.error}")
        bundle = await services.skills()
        original = bundle.models
        bundle.models = FakeLLM(lambda req: text("{}"))
        result = await TextPipeline(services).run(imported.document_id, force=True)
        (out / "replay-page-packs.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
        checks.append(
            {
                "name": "sample replay extraction commits",
                "ok": bool(result["runs"]) and all(r["status"] == "completed" for r in result["runs"]),
                "runs": len(result["runs"]),
            }
        )
        checks.append(
            {
                "name": "bounded reading units",
                "ok": all(
                    unit["estimated_tokens"] <= 7000
                    and estimate_tokens(json.dumps(unit["items"], ensure_ascii=False)) <= 7000
                    for pack in result["page_packs"]
                    for unit in pack["units"]
                ),
            }
        )
        ledger = result["compound_resolution"]
        compounds = ledger["artifacts"][0]["payload"]["compounds"] if ledger["artifacts"] else []
        checks.append(
            {
                "name": "sample evidence-backed 26/UCM-1306 ledger",
                "ok": ledger["status"] == "completed"
                and any(
                    c["primary_label"] == "26" and "UCM-1306" in c["aliases"] and c["evidence"]
                    for c in compounds
                ),
            }
        )
        checks.append(
            {
                "name": "missing P5 dependencies remain pending",
                "ok": any(not pack["ready"] for pack in result["page_packs"])
                and result["status"] == "needs_review",
            }
        )
        async with services.db.session() as session:
            ir = await load_ir(session, imported.document_id)
        candidates = [
            n for n in ir.nodes if n.section_role == "abstract" and "UCM-1306" in (n.text_raw or "")
        ]
        if not candidates:
            raise RuntimeError("sample abstract missing")
        node = candidates[0]
        ctx = await bundle.context_for(
            ToolContext(document_id=imported.document_id, run_id="p6_verify", trace_id="p6_verify"), node.ref
        )
        alias_replay = await bundle.runtime.run("text_extract", node.ref, {}, ctx)
        payload = alias_replay.artifacts[0].payload or {}
        checks.append(
            {
                "name": "sample 26/UCM-1306 rule evidence",
                "ok": any(
                    m["label"] == "26"
                    and "UCM-1306" in m["aliases"]
                    and all(
                        e["node_id"] == node.ref and "26" in (node.text_raw or "")[slice(*e["char_span"])]
                        for e in m["evidence"]
                    )
                    for m in payload.get("compound_mentions", [])
                ),
            }
        )
        if live:
            bundle.models = original
            live_ctx = await bundle.context_for(
                ToolContext(document_id=imported.document_id, run_id="p6_live", trace_id="p6_live"), node.ref
            )
            outcome = await bundle.runtime.run("text_extract", node.ref, {}, live_ctx, force=True)
            record = {
                "status": outcome.status,
                "error": outcome.error,
                "artifacts": [a.model_dump(mode="json") for a in outcome.artifacts],
                "scope": "single sample abstract; no full-text accuracy certification",
            }
            (out / "live-abstract.json").write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n")
            checks.append(
                {
                    "name": "live text task and persistence",
                    "ok": outcome.status == "completed" and bool(outcome.artifacts),
                    "error": outcome.error,
                }
            )
        report = {
            "stage": "p6-text-and-compounds",
            "mode": "replay+live-abstract" if live else "replay",
            "checks": checks,
            "foundation_passed": all(c["ok"] for c in checks),
            "p6_complete": False,
            "p5_real_acceptance": "not_fully_verified",
            "experimental_name_titles": sum(
                bool((a.get("payload") or {}).get("name"))
                for r in result["name_runs"]
                for a in r["artifacts"]
            ),
            "scope": "Sample T1 replay and ledger integration; no full-text model recall or experimental HRMS certification.",
            "opsin_candidates": sum(
                len((a.get("payload") or {}).get("structures", []))
                for r in result["name_runs"]
                for a in r["artifacts"]
            ),
            "opsin_configured": bool(opsin_url),
            "remaining": [
                "sample/SI experimental names and HRMS; OPSIN production service",
                "sample real P5 ready reports and full Reader content acceptance",
                "10 independent paper predictions for silver evaluation",
            ],
        }
        (out / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return bool(report["foundation_passed"])
    finally:
        if original is not None:
            (await services.skills()).models = original
        await services.aclose()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=ROOT / "samples/outputs/phase-6/20261005-foundation")
    parser.add_argument("--live", action="store_true", help="对样例摘要执行一次真实模型任务，独立保存产物")
    parser.add_argument(
        "--full", action="store_true", help="基础重放后增加合成数据的Reader→路径→导出全链路验证"
    )
    parser.add_argument("--opsin-url", default="", help="显式指定名称解析服务，默认不访问外部OPSIN")
    args = parser.parse_args()

    async def run() -> bool:
        passed = await verify(args.out.resolve(), live=args.live, opsin_url=args.opsin_url)
        if args.full:
            from scripts.verify_phase_6_full_text import verify_controlled

            report = await verify_controlled(args.out.resolve() / "controlled-workflow")
            print(json.dumps(report, ensure_ascii=False, indent=2))
            passed = passed and bool(report["workflow_passed"])
        return passed

    return 0 if asyncio.run(run()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
