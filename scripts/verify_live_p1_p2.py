"""真实 P1/P2 验收：模型、MinerU、独立 PostgreSQL 库、API、跨进程恢复。

    uv run python scripts/verify_live_p1_p2.py --direct

读取 .env，但不迁移或清空配置的业务库。创建独立 dp_accept_* 库并保留供复核。
退出码 0 表示所有检查通过；失败、缺配置或跳过均不能视为完整验收通过。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import subprocess
import sys
import time
import uuid
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import sqlalchemy as sa  # noqa: E402

from drugpaper.agent.checkpoint import open_checkpointer  # noqa: E402
from drugpaper.agent.qa import build_ir_qa_agent  # noqa: E402
from drugpaper.config.models_file import default_env, load_default_model  # noqa: E402
from drugpaper.config.settings import Settings  # noqa: E402
from drugpaper.docsrc.ir_source import IrDocumentSource  # noqa: E402
from drugpaper.orchestration.pipeline_graph import DocumentPipeline  # noqa: E402
from drugpaper.persistence.migrate import upgrade_async  # noqa: E402
from drugpaper.services import Services  # noqa: E402
from drugpaper.tracing.db import DbTracer  # noqa: E402
from scripts import verify_phase_1 as p1  # noqa: E402
from scripts import verify_phase_2 as p2  # noqa: E402
from tests.fakes.fake_llm import FakeLLM, call, submit, tools  # noqa: E402
from tests.integration.test_postgres import (  # noqa: E402
    test_ir_roundtrip_and_search_on_postgres,
    test_migrations_up_down_up,
)

CHECKPOINT_RUN = "run_live_acceptance_resume"
SCRIPT_PATH = Path(__file__).resolve()


def redact(value: str) -> str:
    for key, secret in default_env().items():
        if secret and any(
            part in key.upper() for part in ("KEY", "PASSWORD", "SECRET", "DATABASE_URL", "REDIS_URL")
        ):
            value = value.replace(secret, "<redacted>")
    return re.sub(r"(postgresql[^\s]*://[^:\s]+:)[^@\s]+@", r"\1<redacted>@", value)


def create_test_database(configured_url: str) -> tuple[str, str]:
    url = sa.engine.make_url(configured_url)
    if not url.drivername.startswith("postgresql"):
        raise ValueError("真实 PostgreSQL 验收需要配置 DATABASE_URL")
    name = f"dp_accept_{uuid.uuid4().hex[:12]}"
    engine = sa.create_engine(
        url.set(drivername="postgresql+psycopg"),
        isolation_level="AUTOCOMMIT",
        connect_args={"connect_timeout": 5},
    )
    try:
        with engine.connect() as conn:
            conn.execute(sa.text(f'CREATE DATABASE "{name}"'))
    finally:
        engine.dispose()
    return url.set(drivername="postgresql+asyncpg", database=name).render_as_string(hide_password=False), name


async def checkpoint_worker(mode: str, doc_id: str, out: Path) -> None:
    sv = Services.from_settings()
    tracer = DbTracer(sv.db)
    try:
        src = await IrDocumentSource.load(sv.db, doc_id)
        ref = f"{doc_id}/r1/p007.g001.c0"
        if mode == "crash":
            model = FakeLLM(
                [
                    tools(call("get_outline")),
                    tools(call("grep", {"pattern": "Table 3", "node_types": ["caption"]})),
                    RuntimeError("acceptance simulated crash"),
                ]
            )
        else:
            model = FakeLLM(
                [
                    submit(
                        "ADMET Profile for Compound 26",
                        [{"ref": ref, "quote": "ADMET Profile for Compound 26"}],
                    )
                ]
            )
        async with open_checkpointer(sv.settings.database_url) as saver:
            agent = build_ir_qa_agent(src, model, tracer, checkpointer=saver)
            if mode == "crash":
                try:
                    await agent.ask("Table 3 的标题？", run_id=CHECKPOINT_RUN)
                except RuntimeError as exc:
                    if str(exc) != "acceptance simulated crash":
                        raise
                else:
                    raise AssertionError("预期的中断未触发")
            else:
                result = await agent.resume(CHECKPOINT_RUN)
                assert result.stop_reason == "answered" and len(model.requests) == 1
                assert result.tool_calls == 2 and result.evidence[0]["regions"][0]["page"] == 7
                await asyncio.to_thread(
                    out.write_text, json.dumps(result.model_dump(mode="json"), ensure_ascii=False, indent=2)
                )
        await tracer.aclose()
        assert not tracer.errors, tracer.errors
    finally:
        await sv.aclose()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--direct", action="store_true", help="仅本次验收禁用 shell 代理，直连配置的内网服务")
    ap.add_argument("--skip-quality", action="store_true", help="跳过代码检查；报告标记 SKIP，不算完整通过")
    ap.add_argument("--pdf", type=Path, default=next(p2.FIXTURE.glob("*_origin.pdf")))
    ap.add_argument("--output-dir", type=Path)
    ap.add_argument("--checkpoint-worker", choices=["crash", "resume"], help=argparse.SUPPRESS)
    ap.add_argument("--document-id", help=argparse.SUPPRESS)
    args = ap.parse_args()
    if args.direct:
        for key in ("ALL_PROXY", "HTTP_PROXY", "HTTPS_PROXY", "all_proxy", "http_proxy", "https_proxy"):
            os.environ.pop(key, None)
    if args.checkpoint_worker:
        asyncio.run(
            checkpoint_worker(
                args.checkpoint_worker, args.document_id, args.output_dir / "checkpoint_resumed.json"
            )
        )
        return 0

    stamp = datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y%m%d-%H%M%S")
    out = (args.output_dir or ROOT / "samples/outputs/acceptance" / stamp).resolve()
    out.mkdir(parents=True, exist_ok=True)
    work = ROOT / "data/acceptance" / out.name
    work.mkdir(parents=True, exist_ok=True)
    # 配置先解析；验收自身产物保存到仓库，不写外部共享目录。
    settings = Settings()
    os.environ["RUNS_DIR"] = str(work / "runs")
    os.environ["BLOB_ROOT"] = str(work / "blobs")
    p1.OUT_DIR = out / "phase-1"
    p2.OUT_DIR = out / "phase-2"
    p1.OUT_DIR.mkdir(exist_ok=True)
    p2.OUT_DIR.mkdir(exist_ok=True)
    checks: list[dict[str, object]] = []
    try:
        model = load_default_model(settings)
        (out / "model_config.json").write_text(
            json.dumps(
                {
                    "base_url": model.base_url,
                    "model": model.model_name,
                    "api": model.api,
                    "reasoning": model.extra_body.get("reasoning"),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    except Exception:
        pass  # 下方真实模型检查负责记录配置失败，不保存密钥或完整配置。

    def add(name: str, ok: bool | None, detail: str = "") -> None:
        state = "SKIP" if ok is None else "PASS" if ok else "FAIL"
        checks.append({"name": name, "status": state, "detail": redact(detail)})
        print(f"[{state}] {name}: {redact(detail)}", flush=True)
        (out / "report.json").write_text(json.dumps(checks, ensure_ascii=False, indent=2))

    def command(
        name: str, command_args: list[str], env: dict[str, str] | None = None, timeout: int = 300
    ) -> bool:
        try:
            proc = subprocess.run(
                command_args, cwd=ROOT, env=env, capture_output=True, text=True, timeout=timeout
            )
            log = redact(proc.stdout + proc.stderr)
            (out / f"{name}.log").write_text(log)
            add(name, proc.returncode == 0, "\n".join(log.splitlines()[-5:]))
            return proc.returncode == 0
        except Exception as exc:
            add(name, False, f"{type(exc).__name__}: {exc}")
            return False

    if args.skip_quality:
        add("quality", None, "--skip-quality")
    else:
        for name, cmd in [
            ("ruff", ["ruff", "check", "drugpaper", "tests", "scripts"]),
            ("format", ["ruff", "format", "--check", "drugpaper", "tests", "scripts"]),
            ("mypy", ["mypy"]),
        ]:
            command(name, [sys.executable, "-m", *cmd])
        unit_env = dict(
            os.environ,
            DATABASE_URL=f"sqlite+aiosqlite:///{work / 'unit.db'}",
            REDIS_URL="",
            MINERU_URL="",
            MOLPARSER_BASE_URL="",
        )
        command("unit", [sys.executable, "-m", "pytest", "tests/unit", "-q", "--tb=short"], unit_env)

    command("p1-smoke", [sys.executable, "-m", "pytest", "tests/smoke", "-q", "--smoke", "--tb=short"])
    try:
        for check in asyncio.run(p1.qa_checks(p1.real_layout_path(), live=True)):
            add(f"P1 {check.name}", check.ok, check.detail)
    except Exception as exc:
        add("P1 live QA", False, f"{type(exc).__name__}: {exc}")

    database_url = ""
    try:
        database_url, name = create_test_database(settings.database_url)
        add("PostgreSQL independent database", True, name)
    except Exception as exc:
        add("PostgreSQL independent database", False, f"{type(exc).__name__}: {exc}")
    if database_url:
        try:
            asyncio.run(test_migrations_up_down_up(database_url))
            add("PostgreSQL migrations up/down/up", True)
        except Exception as exc:
            add("PostgreSQL migrations up/down/up", False, f"{type(exc).__name__}: {exc}")
    else:
        # PostgreSQL 失败不能挡住对真实 MinerU 和模型的独立诊断。
        database_url = f"sqlite+aiosqlite:///{work / 'live.db'}"

    async def document_checks() -> None:
        await upgrade_async(database_url)
        sv = Services.from_settings(
            Settings(
                database_url=database_url, blob_root=work / "blobs", runs_dir=work / "runs", render_dpi=72
            )
        )
        try:
            offline = await DocumentPipeline(sv).run(offline_dir=p2.FIXTURE)
            assert offline.status == "ready", offline.error
            add(
                "P2 offline fixture IR", True, f"document_id={offline.document_id} nodes={offline.node_count}"
            )
            if sv.db.is_postgres:
                try:
                    await test_ir_roundtrip_and_search_on_postgres((sv, offline.document_id))
                    async with sv.db.session() as session:
                        extensions = {
                            row[0]
                            for row in await session.execute(sa.text("SELECT extname FROM pg_extension"))
                        }
                    add(
                        "PostgreSQL IR / FTS / native pg_trgm",
                        "pg_trgm" in extensions,
                        f"extensions={sorted(extensions)}",
                    )
                except Exception as exc:
                    add("PostgreSQL IR / FTS / native pg_trgm", False, f"{type(exc).__name__}: {exc}")
                worker_env = dict(os.environ, DATABASE_URL=database_url)
                base = [
                    sys.executable,
                    str(SCRIPT_PATH),
                    "--document-id",
                    offline.document_id,
                    "--output-dir",
                    str(out),
                ]
                crashed = await asyncio.to_thread(
                    command, "checkpoint-crash", [*base, "--checkpoint-worker", "crash"], worker_env
                )
                if crashed:
                    await asyncio.to_thread(
                        command, "checkpoint-resume", [*base, "--checkpoint-worker", "resume"], worker_env
                    )
            for check in await p2.agent_checks(sv, offline.document_id, live=True):
                add(f"P2 {check.name}", check.ok, check.detail)
            await sv.db.dispose()
            for check in await asyncio.to_thread(p2.api_checks, sv, offline.document_id):
                add(f"P2 {check.name}", check.ok, check.detail)
            print("Submitting PDF to real MinerU...", flush=True)
            start = time.monotonic()
            result = await DocumentPipeline(sv).run(
                pdf=args.pdf.read_bytes(), filename=args.pdf.name, force=True
            )
            (out / "online_pipeline.json").write_text(
                json.dumps(result.model_dump(mode="json"), ensure_ascii=False, indent=2)
            )
            add(
                "P2 real MinerU submit / poll / fetch / IR / render",
                result.status == "ready",
                f"status={result.status} nodes={result.node_count} pages={result.page_images} elapsed_s={time.monotonic() - start:.1f} error={result.error}",
            )
            if result.status == "ready":
                p2.OUT_DIR = out / "phase-2-online"
                p2.OUT_DIR.mkdir(exist_ok=True)
                for check in await p2.agent_checks(sv, result.document_id, live=True):
                    add(f"P2 online {check.name}", check.ok, check.detail)
                await sv.db.dispose()
                for check in await asyncio.to_thread(p2.api_checks, sv, result.document_id, result.revision):
                    add(f"P2 online {check.name}", check.ok, check.detail)
        finally:
            await sv.aclose()

    try:
        asyncio.run(document_checks())
    except Exception as exc:
        add("P2 document chain", False, f"{type(exc).__name__}: {exc}")
    passed = bool(checks) and all(c["status"] == "PASS" for c in checks)
    lines = [
        "# P1/P2 真实验收",
        "",
        f"时间（Asia/Shanghai）：{stamp}",
        "",
        f"结论：{'全部通过' if passed else '尚未全部通过'}。业务库未执行迁移或清空；独立验收库保留供复核。",
        "",
        "| 检查 | 状态 | 详情 |",
        "|---|---|---|",
    ]
    for check in checks:
        detail = str(check["detail"]).replace("\n", " ").replace("|", "\\|")
        lines.append(f"| {check['name']} | {check['status']} | {detail} |")
    lines.extend(
        [
            "",
            "边界：默认只有一篇样例；其他排版的人工抽查仍需补齐。跨进程恢复使用 FakeLLM 注入中断，真实问答另行验证。SKIP 不能作为通过。",
        ]
    )
    (out / "report.md").write_text("\n".join(lines) + "\n")
    print(f"Report: {out / 'report.md'}", flush=True)
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
