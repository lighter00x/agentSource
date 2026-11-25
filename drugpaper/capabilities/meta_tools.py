"""能力发现、卡片读取、激活/卸载与按需参考资料读取。"""

from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel, Field

from drugpaper.capabilities.index import Capability, CapabilityIndex
from drugpaper.capabilities.toolset import DisclosureToolset
from drugpaper.tools.protocol import Tool, ToolContext, ToolResult, tool


class FindIn(BaseModel):
    query: str = Field(max_length=512)
    toolkit: str | None = None


class NameIn(BaseModel):
    name: str


class ResourceIn(NameIn):
    section: str
    max_tokens: int = Field(default=1500, ge=1, le=4000)


def make_meta_tools(index: CapabilityIndex, toolset: DisclosureToolset) -> list[Tool]:
    def visible(ctx: ToolContext, name: str) -> Capability:
        for cap in index.visible(ctx.user_id, toolset.audience):
            if cap.name == name:
                return cap
        raise PermissionError("能力不存在或对当前角色不可见")

    @tool("find_capabilities", "按关键词发现可见能力的短描述；发现后用 load_capability 激活所需能力。")
    async def find(ctx: ToolContext, args: FindIn) -> ToolResult:
        visible_caps = index.visible(ctx.user_id, toolset.audience, args.toolkit)
        query = args.query.strip().lower()
        tokens = set(re.findall(r"[a-z0-9_][a-z0-9_.-]*", query))
        for phrase in re.findall(r"[\u4e00-\u9fff]+", query):
            tokens.update(phrase[i : i + n] for n in (2, 3, 4) for i in range(len(phrase) - n + 1))

        def score(cap: Capability) -> int:
            short = (cap.name + " " + cap.l0).lower()
            long = (short + " " + cap.l1 + " " + str(cap.triggers)).lower()
            return (
                (1000 if query == cap.name.lower() else 0)
                + (100 if query and query in short else 0)
                + sum((10 if token in short else 1) * len(token) for token in tokens if token in long)
            )

        caps = sorted((c for c in visible_caps if not query or score(c)), key=lambda c: (-score(c), c.name))
        data: dict[str, Any] = {"capabilities": [{"name": c.name, "description": c.l0} for c in caps[:20]]}
        if not caps:
            data["hint"] = "搜索未匹配，不代表能力不存在；请缩短关键词或按工具包重试。"
            data["toolkits"] = sorted({c.toolkit for c in visible_caps if c.toolkit})
        return ToolResult(ok=True, data=data)

    @tool("describe_capability", "读取能力 L1 卡片；不激活工具。")
    async def describe(ctx: ToolContext, args: NameIn) -> ToolResult:
        cap = visible(ctx, args.name)
        if args.name in ctx.capability_state.setdefault("disclosed", []):
            return ToolResult(ok=True, data={"name": args.name, "already_disclosed": True})
        ctx.capability_state["disclosed"].append(args.name)
        return ToolResult(ok=True, data=cap.card())

    @tool("load_capability", "激活可见能力，下一轮才加入工具列表；最多 6 个，LRU 卸载。")
    async def load(ctx: ToolContext, args: NameIn) -> ToolResult:
        cap = visible(ctx, args.name)
        if cap.invocation != "tool_call":
            return ToolResult(ok=False, error="该能力仅支持内部调用")
        loaded = ctx.capability_state.setdefault("loaded", {})
        loaded[args.name] = ctx.capability_state.get("step", 0)
        evicted = []
        while len(loaded) > toolset.max_loaded:
            oldest = min(loaded, key=lambda n: (loaded[n], list(loaded).index(n)))
            del loaded[oldest]
            evicted.append(oldest)
        return ToolResult(ok=True, data={"loaded": args.name, "evicted": evicted, "card": cap.card()})

    @tool("unload_capability", "卸载会话中已激活的能力。")
    async def unload(ctx: ToolContext, args: NameIn) -> ToolResult:
        visible(ctx, args.name)
        ctx.capability_state.setdefault("loaded", {}).pop(args.name, None)
        return ToolResult(ok=True, data={"unloaded": args.name})

    @tool("read_skill_resource", "按需读取 Skill 的 reference 文件小节（L3），有 token 上限。")
    async def resource(ctx: ToolContext, args: ResourceIn) -> ToolResult:
        cap = visible(ctx, args.name)
        if cap.skill is None:
            return ToolResult(ok=False, error="该能力不是 Skill")
        return ToolResult(
            ok=True, data={"name": args.name, "text": cap.skill.read_resource(args.section, args.max_tokens)}
        )

    return [find, describe, load, unload, resource]
