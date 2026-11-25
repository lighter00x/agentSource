"""Platform OPSIN tool; only the configured service can be used."""

from __future__ import annotations

from collections.abc import Callable

from pydantic import BaseModel, ConfigDict, Field

from drugpaper.chem.opsin import OpsinClient
from drugpaper.tools.protocol import Tool, ToolContext, ToolResult, tool


class NameIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    chemical_name: str = Field(min_length=1, max_length=4000)


def make_opsin_tools(client: Callable[[], OpsinClient]) -> list[Tool]:
    @tool(
        "chem.name_to_structure",
        "用平台 OPSIN 解析系统化学名称；返回 success/warning/failure/unavailable，不推断缺失结构。",
        side_effect="compute",
        timeout_s=120,
    )
    async def parse(ctx: ToolContext, args: NameIn) -> ToolResult:
        result = await client().parse(args.chemical_name)
        return ToolResult(ok=True, data=result.model_dump())

    return [parse]
