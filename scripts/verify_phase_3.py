"""P3 可复现验收：默认离线；--live 增加真实视觉分类基线。"""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from drugpaper.capabilities.router import CapabilityRouter, DisclosureContext  # noqa: E402
from drugpaper.config.settings import Settings  # noqa: E402
from drugpaper.docsrc.ir_source import IrDocumentSource  # noqa: E402
from drugpaper.orchestration.pipeline_graph import DocumentPipeline  # noqa: E402
from drugpaper.services import Services  # noqa: E402
from drugpaper.skills.registry import BUILTIN_ROOT  # noqa: E402
from drugpaper.skills.routing import SkillRouting  # noqa: E402
from drugpaper.tools.protocol import ToolContext  # noqa: E402


async def verify(out: Path, live: bool) -> list[dict]:
    work = ROOT / "data/phase-3" / out.name
    work.mkdir(parents=True, exist_ok=True)
    settings = Settings(
        database_url=f"sqlite+aiosqlite:///{out / 'p3.db'}",
        blob_root=work / "blobs",
        runs_dir=work / "runs",
        skills_root=work / "system_skills",
        redis_url="",
        skills_watch_enabled=False,
    )
    sv = Services.from_settings(settings)
    await sv.db.create_all()
    checks = []

    def add(name, ok, detail):
        checks.append({"name": name, "ok": ok, "detail": detail})
        print(f"[{'PASS' if ok else 'FAIL'}] {name}: {detail}", flush=True)

    try:
        bundle = await sv.skills()
        rows = await bundle.registry.versions()
        add(
            "builtin replay cases",
            len(rows) >= 4 and all(r.test_report.get("ok") for r in rows),
            [(r.name, r.status) for r in rows],
        )
        root = settings.skills_root
        root.mkdir(parents=True, exist_ok=True)
        shutil.copytree(ROOT / "tests/fixtures/skills/demo_prompt", root / "demo_prompt", dirs_exist_ok=True)
        await bundle.watcher.reload(publish=False)
        demo = await bundle.registry.resolve("demo_prompt")
        add("new package reload without code changes", demo.scope == "system", demo.skill_id)
        routing_skills = {s.name: s for s in await bundle.registry.list_visible()}
        from drugpaper.skills.loader import load_package

        routing_skills["figure_describe"] = load_package(BUILTIN_ROOT / "figure_describe")
        chain = SkillRouting.from_file(ROOT / "config/routing.yaml", routing_skills).resolve_chain(
            "property_plot"
        )
        add("routing + merged schema", "chart_digitize+property_plot_extract" in chain, chain)
        disclosure = CapabilityRouter(bundle.index).preselect(
            DisclosureContext(intents=["图像分类"], token_budget=1500)
        )
        add(
            "capability preselection budget",
            bool(disclosure.items) and disclosure.total_tokens <= 1500,
            {"items": [i.name for i in disclosure.items], "tokens": disclosure.total_tokens},
        )
        result = await DocumentPipeline(sv, render_pages=False).run(
            offline_dir=ROOT / "tests/fixtures/jmedchem_2c00949"
        )
        add("sample IR", result.status == "ready", result.document_id)
        src = await IrDocumentSource.load(sv.db, result.document_id)
        visual = [n for n in src.ir.nodes if n.node_type in {"figure", "table", "scheme", "figure_part"}]
        add("visual node scope", bool(visual), len(visual))
        if live:
            # 人工预期由测试者复核，未标注项不计入准确率，禁止用模型结果充当标签。
            annotation = ROOT / "samples/outputs/phase-3/manual_classification.csv"
            expected = {}
            if annotation.exists():
                with annotation.open() as f:
                    expected = {
                        r["local_id"]: r["expected_type"] for r in csv.DictReader(f) if r["expected_type"]
                    }
            evaluation = []

            async def classify_node(node):
                ref = src.ref(node.local_id)
                ctx = await bundle.context_for(
                    ToolContext(document_id=src.document_id, run_id="p3_classify", trace_id="p3_classify"),
                    ref,
                )
                # 图注来自 caption 子节点，不以模糊的 figure label 替代。
                captions = [
                    src._by_id[c].text_raw for c in node.children if src._by_id[c].node_type == "caption"
                ]
                if captions:
                    ctx.materials.target["caption"] = "\n".join(t for t in captions if t)
                outcome = await bundle.runtime.run("visual_classify", ref, {}, ctx)
                payload = outcome.artifacts[0].payload if outcome.artifacts else {}
                hit = await bundle.runtime.run("visual_classify", ref, {}, ctx) if outcome.artifacts else None
                wanted = expected.get(node.local_id, "")
                row = {
                    "local_id": node.local_id,
                    "expected_type": wanted,
                    "predicted_type": payload.get("image_type", ""),
                    "match": payload.get("image_type") == wanted if wanted else "",
                    "status": outcome.status,
                    "cached": bool(hit and hit.cached),
                    "artifact_id": outcome.artifacts[0].artifact_id if outcome.artifacts else "",
                    "error": outcome.error or "",
                }
                add(
                    "visual_classify " + node.local_id,
                    outcome.status == "completed" and bool(hit and hit.cached),
                    {k: v for k, v in row.items() if k != "error"} | {"error": outcome.error},
                )
                return row

            semaphore = asyncio.Semaphore(4)

            async def bounded(node):
                async with semaphore:
                    return await classify_node(node)

            evaluation = await asyncio.gather(*(bounded(n) for n in visual))
            with (out / "classify_eval.csv").open("w") as f:
                writer = csv.DictWriter(f, fieldnames=list(evaluation[0]))
                writer.writeheader()
                writer.writerows(evaluation)
            labeled = [r for r in evaluation if r["expected_type"]]
            add(
                "classification baseline",
                bool(labeled),
                {
                    "labeled": len(labeled),
                    "total": len(evaluation),
                    "accuracy": sum(bool(r["match"]) for r in labeled) / len(labeled) if labeled else None,
                },
            )
        else:
            add("live baseline pending", True, "离线验收不宣称真实分类或人工基线通过；使用 --live")
    finally:
        await sv.aclose()
    return checks


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--direct", action="store_true")
    args = parser.parse_args()
    if args.direct:
        for k in ["ALL_PROXY", "all_proxy", "HTTP_PROXY", "http_proxy", "HTTPS_PROXY", "https_proxy"]:
            os.environ.pop(k, None)
    stamp = datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y%m%d-%H%M%S")
    out = ROOT / "samples/outputs/phase-3" / stamp
    out.mkdir(parents=True)
    checks = []
    unit_env = dict(
        os.environ,
        DATABASE_URL=f"sqlite+aiosqlite:///{out / 'unit.db'}",
        REDIS_URL="",
        MINERU_URL="",
        MOLPARSER_BASE_URL="",
        BLOB_ROOT=str(out / "unit_blobs"),
        RUNS_DIR=str(out / "unit_runs"),
    )
    for name, command_args in [
        ("ruff", ["ruff", "check", "drugpaper", "tests", "scripts"]),
        ("format", ["ruff", "format", "--check", "drugpaper", "tests", "scripts"]),
        ("mypy", ["mypy"]),
        ("unit", ["pytest", "tests/unit", "-q", "--tb=short"]),
    ]:
        proc = subprocess.run(
            [sys.executable, "-m", *command_args], cwd=ROOT, env=unit_env, capture_output=True, text=True
        )
        (out / f"{name}.log").write_text(proc.stdout + proc.stderr)
        checks.append(
            {
                "name": name,
                "ok": proc.returncode == 0,
                "detail": (proc.stdout + proc.stderr).splitlines()[-3:],
            }
        )
        print(f"[{'PASS' if proc.returncode == 0 else 'FAIL'}] {name}", flush=True)
    checks.extend(asyncio.run(verify(out, args.live)))
    (out / "report.json").write_text(json.dumps(checks, ensure_ascii=False, indent=2))
    lines = [
        "# P3 验收报告",
        "",
        "范围：代码质量、完整单元测试、内置包回放、热加载与注册、路由和预选。动态调用、权限、LRU、失败回滚由单元测试验证。",
        "",
        "| 检查 | 结果 | 详情 |",
        "|---|---|---|",
    ]
    for c in checks:
        lines.append(
            f"| {c['name']} | {'PASS' if c['ok'] else 'FAIL'} | {str(c['detail']).replace('|', '/')} |"
        )
    (out / "report.md").write_text("\n".join(lines) + "\n")
    print(f"Report: {out / 'report.md'}")
    return int(not all(c["ok"] for c in checks))


if __name__ == "__main__":
    raise SystemExit(main())
