"""P2 阶段验证：一次性跑完全部验收项并输出报告（执行方案 S2.14）。

    uv run python scripts/verify_phase_2.py [--smoke] [--skip-lint] [--skip-integration]

步骤：lint → 单元测试 → 集成测试（PostgreSQL：TEST_DATABASE_URL 或嵌入式 pgserver）→ 迁移（SQLite 临时库）
→ 离线导入样例论文 → 比对 IR 快照 → 逐条检查 F1～F9 → API 取树、节点、页面图、裁剪图、检索
→ 两个 Agent 冒烟问题（--smoke 用真实模型，否则 FakeLLM）→ runs 表 → 接口文档 → 验收清单。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import subprocess
import sys
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import orjson  # noqa: E402
import sqlalchemy as sa  # noqa: E402

from drugpaper.agent.qa import build_ir_qa_agent  # noqa: E402
from drugpaper.config.models_file import ModelsConfigError, load_default_model  # noqa: E402
from drugpaper.config.settings import Settings, get_settings  # noqa: E402
from drugpaper.docsrc.ir_source import IrDocumentSource  # noqa: E402
from drugpaper.domain import ids  # noqa: E402
from drugpaper.domain.nodes import IRDocument  # noqa: E402
from drugpaper.gateway.base import ChatRequest, ChatResponse, ModelClient  # noqa: E402
from drugpaper.gateway.openai_compat import OpenAICompatClient  # noqa: E402
from drugpaper.orchestration.pipeline_graph import DocumentPipeline  # noqa: E402
from drugpaper.parsers.mineru_legacy.snapshot import ir_snapshot  # noqa: E402
from drugpaper.persistence.migrate import downgrade, upgrade  # noqa: E402
from drugpaper.persistence.models import RunRow  # noqa: E402
from drugpaper.persistence.repos.nodes import load_ir  # noqa: E402
from drugpaper.services import Services  # noqa: E402
from drugpaper.text.grep import GrepEngine, GrepQuery  # noqa: E402
from drugpaper.tracing.db import DbTracer  # noqa: E402
from tests.fakes.fake_llm import FakeLLM, call, submit, tools  # noqa: E402

FIXTURE = ROOT / "tests" / "fixtures" / "jmedchem_2c00949"
SNAPSHOT = FIXTURE / "ir_snapshot.json"
OUT_DIR = ROOT / "samples" / "outputs" / "phase-2"
QUESTIONS = [
    ("q1_table3_title", "文中 Table 3 的标题是什么？"),
    ("q2_first_mention_26", "化合物 26 在正文（不含摘要）中第一次出现在哪里？"),
    ("q3_first_mention_26_fulltext", "化合物 26 在包含摘要的全文中第一次出现在哪里？"),
]


@dataclass
class Check:
    name: str
    ok: bool | None
    detail: str = ""


def run_cmd(args: list[str]) -> tuple[bool, str]:
    p = subprocess.run(args, cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace")
    lines = (p.stdout + p.stderr).strip().splitlines()
    tail = next(
        (ln for ln in reversed(lines) if "passed" in ln or "failed" in ln or "error" in ln.lower()),
        lines[-1] if lines else "",
    )
    return p.returncode == 0, tail.strip()


def py(*args: str) -> list[str]:
    return [sys.executable, "-m", *args]


# ---------------------------------------------------------------- F1～F9


def feature_checks(ir: IRDocument) -> list[Check]:
    idx = ir.index()
    out: list[Check] = []

    def add(name: str, fn: Callable[[], tuple[bool, str]]) -> None:
        try:
            ok, detail = fn()
        except Exception as e:  # noqa: BLE001
            ok, detail = False, f"{type(e).__name__}: {e}"
        out.append(Check(name, ok, detail))

    def f1() -> tuple[bool, str]:
        tables = {n.label: n for n in ir.of_type("table")}
        ok = set(tables) == {"Table 1", "Table 2", "Table 3"} and all(
            t.attributes.get("image_blob") for t in tables.values()
        )
        return ok, f"tables={sorted(tables)}，均有图片资产={ok}"

    def f2() -> tuple[bool, str]:
        t1 = idx["p003.g000"]
        return (
            t1.node_type == "table" and t1.label == "Table 1",
            f"p003.g000 type={t1.node_type} label={t1.label}",
        )

    def f3() -> tuple[bool, str]:
        t1 = idx["p003.g000"]
        cap = idx["p003.g000.c0"]
        ok = "caption_mismatch" in t1.quality_flags and (cap.text_norm or "").startswith("Table 1.")
        return (
            ok,
            f"图注来自成员 title；caption_text={t1.source_ref.get('caption_text')!r} 标记 caption_mismatch",
        )

    def f4() -> tuple[bool, str]:
        ab = idx["p000.b007"]
        ok = (
            ab.node_type == "paragraph"
            and "absorbed_by_merge" in ab.quality_flags
            and ab.parent_id != "p000.g007"
        )
        return ok, f"摘要 p000.b007 独立成段，flags={ab.quality_flags}"

    def f56() -> tuple[bool, str]:
        regs = [(r.page, r.role, r.source_bbox_pt) for r in idx["p000.b011"].geometry.regions]
        ok = len(regs) == 2 and regs[1][2] == (314.0, 486.0, 556.0, 630.0) and "p000.b012" not in idx
        ok = ok and idx["p000.b013"].attributes.get("continuation_blocks") == ["p001.b000"]
        return ok, f"p000.b011 regions={[(p, r) for p, r, _ in regs]}；p001.b000 → p000.b013 续段"

    def f7() -> tuple[bool, str]:
        res = GrepEngine(ir.nodes).grep(GrepQuery(pattern="D1 receptor"))
        m = next((x for x in res.matches if x.local_id == "p000.b007"), None)
        ok = (
            m is not None
            and "\\mathrm" in idx["p000.b007"].text_raw
            and m.match.startswith("D")
            and "_" in m.match
        )
        return ok, f"grep('D1 receptor') 命中摘要，映射回原文 {m.match if m else None!r}"

    def f9() -> tuple[bool, str]:
        d = ir.of_type("discarded")
        return bool(d) and all(n.parent_id is None for n in d), f"discarded 节点 {len(d)} 个，均不在主树"

    def sections() -> tuple[bool, str]:
        labels = {idx[c].label for c in idx[ids.ROOT_ID].children}
        need = {"INTRODUCTION", "RESULTS AND DISCUSSION", "CONCLUSIONS", "EXPERIMENTAL SECTION"}
        return need <= labels, f"章节：{sorted(x for x in labels if x)}"

    for name, fn in [
        ("F1 表格无 HTML → table 节点 + 图片资产", f1),
        ("F2 SAR 表合并为 image_body → table / Table 1", f2),
        ("F3 图注错位：标签来自成员 title", f3),
        ("F4 摘要被合并组吞掉 → 独立段落 + absorbed_by_merge", f4),
        ("F5/F6 续段：p0 左栏段落 2 个 region，空块不成节点", f56),
        ("F7 规范化：\\mathrm{D}_{1} → D1，grep 映射回原文", f7),
        ("F9 页眉页脚 → discarded，不在主树", f9),
        ("章节树：INTRODUCTION / RESULTS AND DISCUSSION / CONCLUSIONS / EXPERIMENTAL SECTION", sections),
    ]:
        add(name, fn)
    return out


# ---------------------------------------------------------------- Agent


def offline_model(question: str, doc_id: str, revision: int = 1) -> Callable[[ChatRequest], ChatResponse]:
    ref = f"{doc_id}/r{revision}/"

    def step(req: ChatRequest) -> ChatResponse:
        tool_msgs = [m for m in req.messages if m.role == "tool"]
        if "Table 3" in question:
            if not tool_msgs:
                return tools(call("grep", {"pattern": "Table 3", "node_types": ["caption"]}))
            return submit(
                "Table 3 的标题是 “ADMET Profile for Compound 26”（化合物 26 的 ADMET 性质表）。",
                [{"ref": ref + "p007.g001.c0", "quote": "ADMET Profile for Compound 26"}],
            )
        if not tool_msgs:
            # 数字 26 也可能是参考文献序号；问的是化合物，必须按实体检索。
            args: dict[str, object] = {"pattern": r"\b26\b", "sections": ["abstract"], "max_results": 3}
            if "不含摘要" in question:
                args["sections"] = ["introduction"]
                args["pattern"] = r"compound\s+26"
            return tools(call("grep", args))
        first = json.loads(tool_msgs[-1].text())["data"]["matches"][0]
        return submit(
            f"化合物 26 首次出现在第 {first['page'] + 1} 页（与代号 UCM-1306 一起出现）。",
            [{"ref": first["node_id"]}],
        )

    return step


async def agent_checks(sv: Services, doc_id: str, live: bool, revision: int | None = None) -> list[Check]:
    checks: list[Check] = []
    src = await IrDocumentSource.load(sv.db, doc_id, revision=revision)
    client: OpenAICompatClient | None = None
    if live:
        try:
            client = OpenAICompatClient(load_default_model(get_settings()))
        except ModelsConfigError as e:
            checks.append(Check("真实模型端点", False, str(e)))
            return checks
    mode = f"live:{client.model.model_name}" if client else "offline:FakeLLM"
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    tracer = DbTracer(sv.db)
    try:
        for key, question in QUESTIONS:
            model: ModelClient = client or FakeLLM(offline_model(question, doc_id, src.revision))
            agent = build_ir_qa_agent(src, model, tracer)
            res = await agent.ask(question)
            ev = res.evidence
            ok = res.stop_reason == "answered" and bool(ev) and all(e.get("regions") for e in ev)
            if "Table 3" in question:
                ok = ok and any(e["node_id"].split("/")[-1].startswith("p007.g001") for e in ev)
                ok = ok and "admet profile for compound 26" in str(res.answer.get("answer", "")).lower()
            elif "不含摘要" in question:
                ok = ok and any(e["node_id"].endswith("/p001.b002") for e in ev)
            else:
                ok = ok and any(e["node_id"].endswith("/p000.b007") for e in ev)
            suffix = "" if client else ".offline"
            (OUT_DIR / f"{key}{suffix}.json").write_bytes(
                orjson.dumps(
                    {"mode": mode, "question": question, "result": res.model_dump(mode="json")},
                    option=orjson.OPT_INDENT_2,
                )
            )
            pages = [r["page"] for e in ev for r in e.get("regions", [])[:1]]
            checks.append(
                Check(
                    f"{question}（{mode}）",
                    ok,
                    f"stop={res.stop_reason} 引用={[e['node_id'].split('/')[-1] for e in ev]} 页码={pages}",
                )
            )
        await tracer.aclose()
        async with sv.db.session() as s:
            n_top = (
                await s.execute(
                    sa.select(sa.func.count()).select_from(RunRow).where(RunRow.run_type == "agent")
                )
            ).scalar()
            n_child = (
                await s.execute(
                    sa.select(sa.func.count()).select_from(RunRow).where(RunRow.parent_run_id.is_not(None))
                )
            ).scalar()
        checks.append(
            Check(
                "runs 表记录 Agent 运行（顶层 + 子 Run）",
                bool(n_top and n_child) and not tracer.errors,
                f"agent={n_top} 子 Run={n_child}",
            )
        )
    finally:
        if client is not None:
            await client.aclose()
    return checks


# ---------------------------------------------------------------- API


def api_checks(sv: Services, doc_id: str, revision: int = 1) -> list[Check]:
    from fastapi.testclient import TestClient

    from drugpaper.api.app import create_app
    from drugpaper.render.pages import png_size

    checks: list[Check] = []
    api = "/api/v1"
    with TestClient(create_app(sv)) as c:
        tree = c.get(f"{api}/documents/{doc_id}/tree")
        checks.append(Check("API 文档树", tree.status_code == 200 and bool(tree.json()["root"]["children"])))
        node = c.get(f"{api}/nodes/{doc_id}/r{revision}/p007.g001")
        checks.append(
            Check(
                "API 节点详情（Table 3）", node.status_code == 200 and node.json().get("label") == "Table 3"
            )
        )
        img = c.get(f"{api}/documents/{doc_id}/pages/7/image", params={"dpi": 72})
        checks.append(
            Check(
                "API 页面图",
                img.status_code == 200 and png_size(img.content)[0] > 500,
                f"{png_size(img.content) if img.status_code == 200 else img.status_code}",
            )
        )
        crop = c.get(f"{api}/nodes/{doc_id}/r{revision}/p007.g001/crop", params={"dpi": 144})
        crop.raise_for_status()
        (OUT_DIR / "table3_crop.png").write_bytes(crop.content)
        checks.append(
            Check(
                "API 裁剪图（Table 3）",
                crop.headers["content-type"] == "image/png",
                f"{png_size(crop.content)} → {OUT_DIR / 'table3_crop.png'}",
            )
        )
        s = c.get(f"{api}/documents/{doc_id}/search", params={"q": "hERG", "mode": "fts"})
        checks.append(Check("API 检索（fts: hERG）", s.status_code == 200 and bool(s.json()["results"])))
        assert c.portal is not None
        c.portal.call(sv.db.dispose)
    return checks


# ---------------------------------------------------------------- main


async def pipeline_and_checks(workdir: Path, live: bool) -> list[Check]:
    checks: list[Check] = []
    url = f"sqlite+aiosqlite:///{workdir / 'p2.db'}"
    await asyncio.to_thread(upgrade, url)
    await asyncio.to_thread(downgrade, url)
    await asyncio.to_thread(upgrade, url)
    checks.append(Check("迁移可升可降（SQLite 临时库）", True))

    sv = Services.from_settings(
        Settings(database_url=url, blob_root=workdir / "blobs", render_dpi=72, runs_dir=workdir / "runs")
    )
    res = await DocumentPipeline(sv).run(offline_dir=FIXTURE)
    checks.append(
        Check(
            "离线导入 → 解析 → IR 入库 → 渲染",
            res.status == "ready",
            f"{res.document_id} nodes={res.node_count} pages={res.page_images} {res.timings_ms}",
        )
    )
    async with sv.db.session() as s:
        ir = await load_ir(s, res.document_id)
    snap = ir_snapshot(ir)
    expected = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
    same = snap["nodes"] == expected["nodes"] and snap["type_counts"] == expected["type_counts"]
    checks.append(
        Check("IR 快照与人工核对版本一致", same, f"{snap['node_count']} 节点 {snap['type_counts']}")
    )
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "ir_summary.json").write_bytes(
        orjson.dumps(
            {"meta": snap["meta"], "type_counts": snap["type_counts"], "quality": snap["quality"]},
            option=orjson.OPT_INDENT_2,
        )
    )
    checks.extend(feature_checks(ir))
    checks.extend(await agent_checks(sv, res.document_id, live))
    await sv.db.dispose()
    checks.extend(await asyncio.to_thread(api_checks, sv, res.document_id))
    return checks


def main() -> int:
    ap = argparse.ArgumentParser(description="P2 阶段验证")
    ap.add_argument("--smoke", action="store_true", help="Agent 冒烟问题使用真实模型（需要 .env）")
    ap.add_argument("--skip-lint", action="store_true")
    ap.add_argument("--skip-integration", action="store_true")
    args = ap.parse_args()

    checks: list[Check] = []
    if not args.skip_lint:
        for name, cmd in [
            ("ruff check", py("ruff", "check", "drugpaper", "tests", "scripts")),
            ("ruff format --check", py("ruff", "format", "--check", "drugpaper", "tests", "scripts")),
            ("mypy (drugpaper strict)", py("mypy")),
        ]:
            ok, tail = run_cmd(cmd)
            checks.append(Check(name, ok, tail))
    ok, tail = run_cmd(py("pytest", "tests/unit", "-q", "-p", "no:cacheprovider"))
    checks.append(Check("单元测试", ok, tail))
    if args.skip_integration:
        checks.append(Check("集成测试（PostgreSQL）", None, "--skip-integration"))
    else:
        ok, tail = run_cmd(
            py("pytest", "tests/integration", "-q", "-m", "integration", "-p", "no:cacheprovider")
        )
        checks.append(Check("集成测试（PostgreSQL：迁移、FTS、runs、checkpointer 跨进程恢复）", ok, tail))

    with tempfile.TemporaryDirectory(prefix="dp_verify_p2_") as tmp:
        checks.extend(asyncio.run(pipeline_and_checks(Path(tmp), live=args.smoke)))

    iface = ROOT / "docs" / "interfaces"
    checks.append(Check("接口文档 document-ir.md", (iface / "document-ir.md").exists()))
    checks.append(Check("OpenAPI openapi-p2.json", (iface / "openapi-p2.json").exists()))

    print("\nP2 验收清单")
    print("=" * 78)
    for c in checks:
        mark = "SKIP" if c.ok is None else ("PASS" if c.ok else "FAIL")
        print(f"[{mark}] {c.name}")
        if c.detail:
            print(f"       {c.detail}")
    failed = [c for c in checks if c.ok is False]
    print("=" * 78)
    print("全部通过" if not failed else f"{len(failed)} 项未通过")
    return 1 if failed else 0


def _unused(_: Any) -> None:  # 保持导入稳定（mypy 不检查 scripts/）
    return None


if __name__ == "__main__":
    raise SystemExit(main())
