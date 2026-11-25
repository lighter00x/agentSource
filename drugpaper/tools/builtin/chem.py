"""独立化学 Tool：供 Python Skill 的白名单调用及 Agent 按需加载。"""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from pydantic import BaseModel, Field

from drugpaper.chem.backfill import backfill
from drugpaper.chem.splicer.adapter import infer_role, substitute
from drugpaper.tools.protocol import Tool, ToolContext, ToolResult, tool


class SubstituteIn(BaseModel):
    caption: str
    definitions: dict[str, str | int | list[str]]
    max_outputs: int = Field(default=64, ge=1, le=1024)


class EsmilesIn(BaseModel):
    esmiles: str


class BackfillIn(BaseModel):
    data: dict[str, Any]
    molecules: dict[int, str]


class DeliveryIn(BaseModel):
    data: dict[str, Any]
    boxes: list[dict[str, Any]]
    structures: list[dict[str, Any]]
    blocked_instance_paths: list[str] = Field(default_factory=list)


def make_chem_tools() -> list[Tool]:
    @tool(
        "markush.substitute",
        "按已给定的取代基拼接 E-SMILES；保持 R 与 R′ 独立，限制枚举数量。",
        side_effect="compute",
        timeout_s=30,
    )
    async def markush(ctx: ToolContext, args: SubstituteIn) -> ToolResult:
        import asyncio

        result = await asyncio.to_thread(
            substitute, args.caption, args.definitions, max_outputs=args.max_outputs
        )
        return ToolResult(ok=result.status == "completed", data=asdict(result), error=result.message)

    @tool(
        "esmiles.infer_role",
        "按 E-SMILES 语法保守判断 scaffold/substituent/sru/unknown。",
        side_effect="compute",
    )
    async def role(ctx: ToolContext, args: EsmilesIn) -> ToolResult:
        return ToolResult(ok=True, data={"role": infer_role(args.esmiles)})

    @tool("esmiles.parse", "解析 E-SMILES 的 SMILES、标注与扩展段；保留原文。", side_effect="compute")
    async def parse(ctx: ToolContext, args: EsmilesIn) -> ToolResult:
        from drugpaper.chem.splicer.vendor import Translator

        parsed = Translator.parse_caption(args.esmiles)
        if parsed is None:
            return ToolResult(ok=False, error="E_INVALID_ESMILES")
        smiles, groups, extension = parsed
        return ToolResult(ok=True, data={"smiles": smiles, "groups": groups, "extension": extension})

    @tool(
        "mol.backfill", "按 mol_id/mol_ids 从给定索引回填 E-SMILES；不修改输入对象。", side_effect="compute"
    )
    async def fill(ctx: ToolContext, args: BackfillIn) -> ToolResult:
        data, errors = backfill(args.data, args.molecules)
        return ToolResult(ok=not errors, data={"result": data, "errors": errors})

    @tool(
        "mol.deliver",
        "按当前图像框和已校验结构确定性回填全部分子引用，保留ID、证据及逐框覆盖错误。计算成功不代表内容验收通过。",
        side_effect="compute",
    )
    async def delivery(ctx: ToolContext, args: DeliveryIn) -> ToolResult:
        import asyncio

        from drugpaper.facts.molecular_delivery import deliver

        data = await asyncio.to_thread(
            deliver,
            args.data,
            args.boxes,
            args.structures,
            blocked_instance_paths=args.blocked_instance_paths,
        )
        return ToolResult(ok=True, data=data)

    return [markush, role, parse, fill, delivery]
