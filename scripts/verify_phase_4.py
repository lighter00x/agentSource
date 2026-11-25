"""真实 P4 验收：隔离 SQLite 库和用户资产，使用已部署 GPU Worker/MolParser。"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from sqlalchemy import select

from drugpaper.chem.normalize import canonical
from drugpaper.config.settings import Settings
from drugpaper.domain.ids import new_id
from drugpaper.orchestration.molecule_pipeline import MoleculePipeline
from drugpaper.orchestration.pipeline_graph import DocumentPipeline
from drugpaper.persistence.migrate import upgrade_async
from drugpaper.persistence.models import NodeRow, StructureCandidateRow
from drugpaper.services import Services
from drugpaper.tools.protocol import ToolContext
from scripts.debug_molecule_overlay import overlay

GOLDEN = {"3": "O=Cc1ccc(-c2ccccc2OCF)cc1", "26": "FCOc1ccccc1-c1ccc(S(C)(=N)=O)cc1"}


async def verify(output: Path, *, offline_dir: Path, run_tests: bool = True) -> dict[str, object]:
    await asyncio.to_thread(output.mkdir, parents=True, exist_ok=True)
    output = await asyncio.to_thread(output.resolve)
    settings = Settings().model_copy(
        update={
            "database_url": f"sqlite+aiosqlite:///{output / 'phase4.db'}",
            "blob_root": output / "blobs",
            "runs_dir": output / "runs",
            "skills_root": output / "skills",
            "redis_url": "",
        }
    )
    await upgrade_async(settings.database_url)
    services = Services.from_settings(settings)
    report: dict[str, object] = {}
    try:
        if run_tests:
            process = await asyncio.create_subprocess_exec(
                sys.executable,
                "-m",
                "pytest",
                "tests/unit/chem",
                "-q",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
            stdout, _ = await process.communicate()
            report["unit_tests"] = {
                "returncode": process.returncode,
                "summary": stdout.decode().strip().splitlines()[-1],
            }
            if process.returncode:
                raise RuntimeError("P4 单元测试失败，停止真实验收")
        report["molparser_health"] = await services.molparser().health()
        imported = await DocumentPipeline(services).run(offline_dir=offline_dir)
        if imported.status != "ready":
            raise RuntimeError(str(imported.error))
        document_id = imported.document_id
        report["document_id"] = document_id
        report["pipeline"] = await MoleculePipeline(services).run(document_id)
        bundle = await services.skills()
        tool_ctx = ToolContext(document_id=document_id, run_id=new_id("run"), trace_id=new_id("tr"))
        async with services.db.session() as session:
            rows = list(
                (
                    await session.execute(
                        select(StructureCandidateRow).where(StructureCandidateRow.is_active.is_(True))
                    )
                ).scalars()
            )
            nodes = {
                (row.document_id, row.revision, row.local_id): row
                for row in (await session.execute(select(NodeRow))).scalars()
            }
        report["recognition"] = {
            "total": len(rows),
            "valid": sum(bool(row.payload["validation"]["valid"]) for row in rows),
        }
        comparisons = {}
        bindings_by_parent = {}
        for label, smiles in GOLDEN.items():
            expected = canonical(smiles).inchikey
            matches = [row for row in rows if row.payload.get("inchikey") == expected]
            comparisons[label] = {
                "expected_inchikey": expected,
                "matched_node_refs": [row.node_ref for row in matches],
                "ok": bool(matches),
            }
            # 已知样例结构的 InChIKey 核对用于验收绑定，不冒充 P5 图像结构化。
            for row in matches[:1]:
                local = row.node_ref.rsplit("/", 1)[-1]
                node = nodes[(document_id, 1, local)]
                parent = f"{document_id}/r1/{node.parent_local_id}"
                bindings_by_parent.setdefault(parent, []).append(
                    {"mol_id": node.attributes["reading_index"], "reference": label}
                )
        for parent, bindings in bindings_by_parent.items():
            ctx = await bundle.context_for(tool_ctx, parent)
            result = await bundle.runtime.run(
                "mol_backfill",
                parent,
                {"image_type": 10, "extraction_payload": {"molecules": bindings}},
                ctx,
                force=True,
            )
            if result.status != "completed":
                raise RuntimeError(result.error)
        report["golden_structures"] = comparisons
        ctx = await bundle.context_for(tool_ctx, document_id)
        resolved = await bundle.runtime.run("compound_resolve", document_id, {}, ctx, force=True)
        report["compound_resolve"] = (
            resolved.artifacts[0].payload if resolved.artifacts else {"error": resolved.error}
        )
        report["overlays"] = await overlay(services, document_id, output / "overlay")
        report["status"] = "passed" if all(item["ok"] for item in comparisons.values()) else "needs_review"
    except Exception as exc:
        report["status"] = "failed"
        report["error"] = f"{type(exc).__name__}: {exc}"
        raise
    finally:
        (output / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str))
        await services.aclose()
    return report


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--offline", type=Path, default=Path("tests/fixtures/jmedchem_2c00949"))
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("samples/outputs/phase-4")
        / datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y%m%d-%H%M%S"),
    )
    args = parser.parse_args()
    result = await verify(args.output, offline_dir=args.offline)
    print(
        json.dumps(
            {"status": result["status"], "report": str(args.output / "report.json")}, ensure_ascii=False
        )
    )


if __name__ == "__main__":
    asyncio.run(main())
