"""测试用 python 型 Skill：统计词数；mode 用来触发各种提交分支。"""

from __future__ import annotations

import asyncio
from typing import Any

from drugpaper.skills.protocol import ArtifactDraft, FactProposal, NodeProposal, SkillContext, SkillResult


async def run(inputs: dict[str, Any], ctx: SkillContext) -> SkillResult:
    mode = inputs.get("mode", "normal")
    words = len(str(inputs["text"]).split())
    payload: dict[str, Any] = {"words": words}
    if mode == "bad_schema":
        payload = {"words": "many"}
    if mode == "slow":
        await asyncio.sleep(10)
    if mode == "tool":
        await ctx.tools.call("get_outline")  # 未在白名单中
    result = SkillResult(artifacts=[ArtifactDraft(artifact_type="word_count", payload=payload)])
    if mode == "facts":
        result.fact_proposals.append(FactProposal(fact_type="measurement", payload={"metric": "words", "value": words}))
    if mode == "bad_fact":
        result.fact_proposals.append(FactProposal(fact_type="compound", payload={}))
    if mode == "node":
        target_local = ctx.target_ref.rsplit("/", 1)[-1]
        result.node_proposals.append(
            NodeProposal(
                local_id=f"{target_local}.m00",
                node_type="molecule",
                parent_local_id=target_local,
                local_bbox=(0.1, 0.1, 0.5, 0.5),
            )
        )
    return result
