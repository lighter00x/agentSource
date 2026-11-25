"""Run a real ReAct agent against an owned test figure; audit persisted output, not its claims."""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from typing import Any

from drugpaper.agent.config import AgentConfig
from drugpaper.agent.runtime import AgentRuntime
from drugpaper.agent.state import Budget
from drugpaper.capabilities.meta_tools import make_meta_tools
from drugpaper.capabilities.toolset import DisclosureToolset
from drugpaper.persistence.models import ArtifactRow
from drugpaper.services import Services
from drugpaper.tools.protocol import ToolContext
from drugpaper.validators.workflow import RequiredCallsValidator
from scripts.ingest_phase5_data import ROOT, settings_for


class Trace:
    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    def event(self, run_id: str, kind: str, payload: dict[str, Any]) -> None:
        self.events.append({"run_id": run_id, "kind": kind, "payload": payload})
        if kind == "tool_call":
            print(
                json.dumps(
                    {k: payload.get(k) for k in ("name", "ok", "error", "latency_ms")}, ensure_ascii=False
                ),
                flush=True,
            )


async def verify(target: str, output: Path, root: Path, gold_path: Path | None = None) -> dict[str, Any]:
    await asyncio.to_thread(output.mkdir, parents=True, exist_ok=False)
    sv = Services.from_settings(settings_for(root))
    trace = Trace()
    try:
        bundle = await sv.skills()
        ctx = ToolContext(
            run_id="autonomy-" + output.name,
            trace_id="autonomy-" + output.name,
            document_id=target.split("/")[0],
        )
        await bundle.context_for(ctx, target)  # owner and target preflight
        toolset = DisclosureToolset(bundle.tools, [], bundle.index)
        for tool in make_meta_tools(bundle.index, toolset):
            bundle.tools.replace(tool)
        system = (
            "你是自主文献图像处理Agent。只有实际工具返回和持久化产物才是完成证据。"
            "自行发现、读取能力卡/资源并逐步加载合适的Skill，不要直接臆造结构化结果。"
            "具有关联先后关系的调用分轮执行；一轮加载后的能力下一轮才能调用。"
            "中间视觉步骤只用mol_id，不将识别SMILES复制给视觉模型。"
            "先检测和识别，生成编号图，然后选一个适合图类的视觉Skill，最后确定性回填。"
            "大产物用artifact_inputs按ID交接，不复制完整JSON。"
            "错误可据证据修改参数或重试，但不能将needs_review/unclear当通过。"
            "最终列出产物ID、完成项与尚需复核的问题；不要停在计划。"
        )
        agent = AgentRuntime(
            AgentConfig(
                name="live_skill_autonomy",
                system_prompt=system,
                toolset=toolset,
                validators=[
                    RequiredCallsValidator(
                        {"molecule_detect", "molecule_recognize", "som_render", "mol_backfill"},
                        any_of=[
                            {
                                s.name
                                for s in await bundle.registry.list_visible()
                                if s.manifest.vision_structuring
                            }
                        ],
                    )
                ],
                max_validation_retries=4,
                budget=Budget(max_steps=48, max_model_calls=50, max_tool_calls=65, max_wall_time_s=1200),
                model_timeout_s=100,
                max_tokens=4096,
            ),
            bundle.models,
            bundle.tools,
            trace,
        )
        result = await agent.run(
            f"请实际处理当前图像 {target}，完成最终分子回填。自行选择正确图类的Skill，检查调用和结果是否可用。",
            ctx,
        )
        successful = {
            e["payload"]["name"] for e in trace.events if e["kind"] == "tool_call" and e["payload"].get("ok")
        }
        required = {"molecule_detect", "molecule_recognize", "som_render", "mol_backfill"}
        handoffs = [
            e["payload"].get("artifact_ids", [])
            for e in trace.events
            if e["kind"] == "tool_call" and e["payload"].get("ok") and e["payload"]["name"] == "mol_backfill"
        ]
        delivery_id = handoffs[-1][0] if handoffs and handoffs[-1] else None
        async with sv.db.session(ctx.user_id) as session:
            delivery = await session.get(ArtifactRow, delivery_id) if delivery_id else None
        visual_names = {s.name for s in await bundle.registry.list_visible() if s.manifest.vision_structuring}
        errors = (delivery.payload or {}).get("errors", []) if delivery else ["no_delivery"]
        registry = (delivery.payload or {}).get("molecule_objects", []) if delivery else []
        report = {
            "status": "passed"
            if required <= successful
            and successful & visual_names
            and delivery
            and delivery.status == "completed"
            and not errors
            else "needs_review",
            "scope": "Real model capability discovery/load/invoke and persistent molecule handoff; independent visual chemistry accuracy not certified",
            "target_ref": target,
            "agent_result": result.model_dump(),
            "successful_calls": sorted(successful),
            "missing_calls": sorted(required - successful),
            "delivery_errors": errors,
            "molecule_object_count": len(registry),
            "delivery_artifact_id": delivery.id if delivery else None,
            "tool_failures": [
                e["payload"] for e in trace.events if e["kind"] == "tool_call" and not e["payload"].get("ok")
            ],
        }
        if delivery and gold_path:
            from scripts.verify_phase5_molecules import compare_figure1

            gold = json.loads(await asyncio.to_thread(gold_path.read_text))
            comparison = compare_figure1(
                {
                    "molecular_coverage": delivery.payload["coverage"],
                    "backfilled_extraction": delivery.payload["payload"],
                },
                gold,
            )
            report["independent_comparison"] = comparison
            if comparison["status"] != "passed":
                report["status"] = "needs_review"
        if delivery:
            (output / "delivery.json").write_text(json.dumps(delivery.payload, ensure_ascii=False, indent=2))
        (output / "trace.json").write_text(json.dumps(trace.events, ensure_ascii=False, indent=2))
        (output / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2))
        print(
            json.dumps(
                {
                    k: report[k]
                    for k in ("status", "missing_calls", "molecule_object_count", "delivery_artifact_id")
                },
                ensure_ascii=False,
            ),
            flush=True,
        )
        return report
    finally:
        await sv.aclose()


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("target")
    parser.add_argument("output", type=Path)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument(
        "--gold", type=Path, help="Optional independent Figure1 count/label/connectivity reference"
    )
    args = parser.parse_args()
    report = await verify(args.target, args.output, args.root, args.gold)
    if report["status"] != "passed":
        raise SystemExit(1)


if __name__ == "__main__":
    asyncio.run(main())
