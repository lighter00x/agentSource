"""确定性预选：可见性 → 精确触发 → 评分 → 按预算降级。"""

from __future__ import annotations

from dataclasses import dataclass, field

from drugpaper.capabilities.assembler import (
    CapabilityCandidate,
    DisclosedItem,
    pack_capabilities,
    render_capability_block,
)
from drugpaper.capabilities.index import CapabilityIndex
from drugpaper.capabilities.tokens import estimate_tokens


@dataclass
class DisclosureContext:
    user_id: str = "default"
    audience: str = "chat"
    failed_checks: list[str] = field(default_factory=list)
    image_types: list[str] = field(default_factory=list)
    intents: list[str] = field(default_factory=list)
    loaded: list[str] = field(default_factory=list)
    token_budget: int = 1500
    toolkit: str | None = None
    invocations: list[str] = field(default_factory=list)
    max_candidates: int | None = None


@dataclass
class Disclosure:
    items: list[DisclosedItem]
    total_tokens: int
    candidates: list[CapabilityCandidate]
    text: str


class CapabilityRouter:
    def __init__(self, index: CapabilityIndex) -> None:
        self.index = index

    def preselect(self, ctx: DisclosureContext) -> Disclosure:
        candidates = []
        for cap in self.index.visible(ctx.user_id, ctx.audience, ctx.toolkit):
            if ctx.invocations and cap.invocation not in ctx.invocations:
                continue
            if cap.name in ctx.loaded:
                continue
            triggers = cap.triggers
            check_hits = set(ctx.failed_checks) & set(triggers.get("check_codes", []))
            # 补救场景必须匹配失败码，避免仅凭关键词混入无关动作。
            if ctx.failed_checks and not check_hits:
                continue
            score = 100 * len(check_hits) + 50 * len(
                set(ctx.image_types) & set(triggers.get("image_types", []))
            )
            score += 10 * sum(
                any(word.lower() in intent.lower() for intent in ctx.intents)
                for word in triggers.get("intents", [])
            )
            if score:
                candidates.append(CapabilityCandidate(cap.name, score, cap.l0, cap.l1))
        candidates.sort(key=lambda c: (-c.score, c.name))
        if ctx.max_candidates is not None:
            candidates = candidates[: ctx.max_candidates]
        for c in candidates[: ctx.max_candidates or 3]:
            c.level = "L1"
        items, _ = pack_capabilities(candidates, ctx.token_budget)
        text = render_capability_block(candidates, items)
        return Disclosure(items, estimate_tokens(text), candidates, text)
