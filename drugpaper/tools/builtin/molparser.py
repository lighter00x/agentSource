"""将已有 MolParser 客户端注入为 Tool；不接受任意本地路径。"""

from __future__ import annotations

from collections.abc import Callable

from pydantic import BaseModel, Field

from drugpaper.gateway.molparser import MolParserClient, extract_esmiles
from drugpaper.tools.protocol import Tool, ToolContext, ToolResult, tool


class EmptyIn(BaseModel):
    pass


class RecognizeIn(BaseModel):
    images_b64: list[str] = Field(min_length=1, max_length=32)


def make_molparser_tools(client: Callable[[], MolParserClient]) -> list[Tool]:
    @tool("molparser.health", "检查已配置的 MolParser 服务健康状态。", side_effect="external_model")
    async def health(ctx: ToolContext, args: EmptyIn) -> ToolResult:
        return ToolResult(ok=True, data=await client().health())

    @tool(
        "molparser.recognize",
        "识别最多 32 张 base64 分子图片，保留 input_index 和 E-SMILES 原文。",
        side_effect="external_model",
        timeout_s=120,
    )
    async def recognize(ctx: ToolContext, args: RecognizeIn) -> ToolResult:
        results = await client().recognize(args.images_b64)
        esmiles = [extract_esmiles([row])[0] if not row.get("error") else None for row in results]
        return ToolResult(ok=True, data={"results": results, "esmiles": esmiles})

    return [health, recognize]
