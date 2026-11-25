"""P1 阶段验证：一次性跑完全部验收项并输出报告（执行方案 S1.12）。

    uv run python scripts/verify_phase_1.py [--smoke] [--layout PATH]

步骤：
1. lint：ruff check、ruff format --check、mypy；
2. 单元测试；
3. （可选，--smoke）真实端点冒烟测试；
4. max_steps=1 跑一次问答，确认正常结束并给出"预算用尽"的答案
   （配置了真实端点时用真实模型，否则用 FakeLLM 离线验证）；
5. 两个冒烟问题的输出保存到 samples/outputs/phase-1/（离线模式下保存 FakeLLM 演示结果并标注）；
6. 打印验收清单。
"""

from __future__ import annotations

import argparse
import asyncio
import re
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import orjson  # noqa: E402

from drugpaper.agent.qa import build_qa_agent  # noqa: E402
from drugpaper.agent.state import AgentRunResult, Budget  # noqa: E402
from drugpaper.config.models_file import ModelsConfigError, load_default_model  # noqa: E402
from drugpaper.config.settings import get_settings  # noqa: E402
from drugpaper.gateway.base import ChatRequest, ChatResponse, ModelClient  # noqa: E402
from drugpaper.gateway.openai_compat import OpenAICompatClient  # noqa: E402
from drugpaper.tracing.jsonl import JsonlTracer, read_events  # noqa: E402
from tests.conftest import MINI_LAYOUT, real_layout_path  # noqa: E402
from tests.fakes.fake_llm import FakeLLM, call, submit, tools  # noqa: E402

OUT_DIR = ROOT / "samples" / "outputs" / "phase-1"
QUESTIONS = [
    ("q1_compound_number", "UCM-1306 对应论文中的哪个化合物编号？"),
    ("q2_target_receptor", "这篇论文的研究对象是什么受体？"),
]


@dataclass
class Check:
    name: str
    ok: bool | None  # None = 跳过
    detail: str = ""


def run_cmd(args: list[str]) -> tuple[bool, str]:
    proc = subprocess.run(args, cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace")
    out = (proc.stdout + proc.stderr).strip().splitlines()
    return proc.returncode == 0, out[-1] if out else ""


def py(*args: str) -> list[str]:
    return [sys.executable, "-m", *args]


# ---------------------------------------------------------------- 离线 FakeLLM 脚本


def offline_script(question: str) -> Callable[[ChatRequest], ChatResponse]:
    """按问题模拟"grep → get_block → submit_answer"的检索过程；只绑定 submit_answer 时直接作答。"""
    pattern, answer = (
        ("UCM-1306", "UCM-1306 即化合物 26。") if "UCM" in question else ("D1", "多巴胺 D1 受体。")
    )
    state = {"n": 0}

    def step(req: ChatRequest) -> ChatResponse:
        state["n"] += 1
        names = [t.name for t in req.tools]
        if names == ["submit_answer"] or state["n"] >= 3:
            return submit(answer, [{"ref": "p000.b007"}])
        if state["n"] == 1:
            return tools(call("grep", {"pattern": pattern}))
        return tools(call("get_block", {"block_id": "p000.b007"}))

    return step


def live_client() -> OpenAICompatClient | None:
    try:
        return OpenAICompatClient(load_default_model(get_settings()))
    except ModelsConfigError:
        return None


async def ask(
    model: ModelClient, layout: Path, question: str, budget: Budget, tracer: JsonlTracer
) -> AgentRunResult:
    agent = build_qa_agent(layout, model, tracer, budget=budget)
    return await agent.ask(question)


def trace_complete(tracer: JsonlTracer, run_id: str) -> bool:
    kinds = [e["kind"] for e in read_events(tracer.path_for(run_id))]
    return bool(kinds) and kinds[0] == "run_start" and kinds[-1] == "run_end" and "model_call" in kinds


async def qa_checks(layout: Path, live: bool) -> list[Check]:
    settings = get_settings()
    tracer = JsonlTracer(settings.runs_dir)
    client = live_client() if live else None
    if live and client is None:
        return [Check("真实模型端点", False, "模型配置无法解析；--smoke 禁止回退到 FakeLLM")]
    mode = f"live:{client.model.model_name}" if client else "offline:FakeLLM"
    checks: list[Check] = []
    try:
        # max_steps=1：必须正常结束，并给出"预算用尽"的答案
        model: ModelClient = client or FakeLLM(offline_script(QUESTIONS[0][1]))
        res = await ask(model, layout, QUESTIONS[0][1], Budget(max_steps=1), tracer)
        ok = res.stop_reason == "budget_exhausted" or (res.stop_reason == "answered" and res.steps <= 1)
        checks.append(
            Check(f"max_steps=1 正常结束（{mode}）", ok, f"stop_reason={res.stop_reason} steps={res.steps}")
        )

        # 两个冒烟问题：5 步以内给出带有效引用的答案，并保存输出
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        for key, question in QUESTIONS:
            model = client or FakeLLM(offline_script(question))
            res = await ask(model, layout, question, Budget(), tracer)
            valid = [c["ref"] for c in res.answer.get("citations", []) if c.get("ref") in set(res.seen_refs)]
            record: dict[str, Any] = {
                "mode": mode,
                "layout": str(layout.relative_to(ROOT) if layout.is_relative_to(ROOT) else layout),
                "question": question,
                "result": res.model_dump(mode="json"),
                "trace_file": str(tracer.path_for(res.run_id)),
            }
            suffix = "" if client else ".offline"
            (OUT_DIR / f"{key}{suffix}.json").write_bytes(orjson.dumps(record, option=orjson.OPT_INDENT_2))
            ok = res.stop_reason == "answered" and res.steps <= 5 and bool(valid)
            answer = str(res.answer.get("answer", ""))
            ok = ok and (
                "26" in answer if key == "q1_compound_number" else bool(re.search(r"D\s*[1₁]", answer))
            )
            checks.append(
                Check(
                    f"{question}（{mode}）",
                    ok,
                    f"stop_reason={res.stop_reason} steps={res.steps} 有效引用={valid} 答案={res.answer.get('answer')!r}",
                )
            )
            checks.append(Check(f"JSONL 追踪完整：{res.run_id}", trace_complete(tracer, res.run_id)))
    finally:
        if client is not None:
            await client.aclose()
    return checks


def main() -> int:
    parser = argparse.ArgumentParser(description="P1 阶段验证")
    parser.add_argument("--smoke", action="store_true", help="同时运行真实端点冒烟测试并用真实模型跑问答")
    parser.add_argument(
        "--layout", type=Path, default=None, help="layout.json 路径（默认：真实样例 > 合成数据）"
    )
    args = parser.parse_args()

    checks: list[Check] = []
    for name, cmd in [
        ("ruff check", py("ruff", "check", "drugpaper", "tests", "scripts")),
        ("ruff format --check", py("ruff", "format", "--check", "drugpaper", "tests", "scripts")),
        ("mypy (drugpaper strict)", py("mypy")),
        ("单元测试", py("pytest", "tests/unit", "-q")),
    ]:
        ok, tail = run_cmd(cmd)
        checks.append(Check(name, ok, tail))

    if args.smoke:
        ok, tail = run_cmd(py("pytest", "tests/smoke", "-q", "--smoke"))
        checks.append(Check("冒烟测试（真实端点）", ok, tail))
    else:
        checks.append(Check("冒烟测试（真实端点）", None, "未启用，使用 --smoke 运行"))

    layout = args.layout or real_layout_path() or MINI_LAYOUT
    if layout == MINI_LAYOUT:
        checks.append(
            Check("样例论文 fixtures", None, "tests/fixtures/jmedchem_2c00949 缺失，问答使用合成数据")
        )
    checks.extend(asyncio.run(qa_checks(layout, live=args.smoke)))

    interfaces_doc = ROOT / "docs" / "interfaces" / "agent-runtime.md"
    checks.append(Check("接口文档 docs/interfaces/agent-runtime.md", interfaces_doc.exists()))

    print("\nP1 验收清单")
    print("=" * 72)
    for c in checks:
        mark = "SKIP" if c.ok is None else ("PASS" if c.ok else "FAIL")
        print(f"[{mark}] {c.name}")
        if c.detail:
            print(f"       {c.detail}")
    failed = [c for c in checks if c.ok is False]
    print("=" * 72)
    print("全部通过" if not failed else f"{len(failed)} 项未通过")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
