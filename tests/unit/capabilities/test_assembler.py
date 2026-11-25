"""S3.6：分层 prompt 与 PromptAssembler。"""

from __future__ import annotations

import pytest

from drugpaper.agent.state import initial_state
from drugpaper.capabilities.assembler import (
    CapabilityCandidate,
    LayeredContextAssembler,
    PromptAssembler,
    PromptBudgetError,
    PromptParts,
    load_fragment,
    pack_capabilities,
)
from drugpaper.capabilities.tokens import estimate_tokens
from drugpaper.gateway.base import ImagePart, Message, TextPart

FRAGMENTS = [
    "common/data_boundary",
    "common/mol_reference",
    "common/numeric_transcription",
    "common/no_hallucination",
]


def cands(n: int, l1_len: int = 300) -> list[CapabilityCandidate]:
    return [
        CapabilityCandidate(
            name=f"cap{i}", score=100 - i, l0=f"能力 {i} 的一句话说明", l1="细节" * l1_len, level="L1"
        )
        for i in range(n)
    ]


def test_common_fragments_exist_and_small() -> None:
    for f in FRAGMENTS:
        text = load_fragment(f)
        assert text.startswith("## ") and estimate_tokens(text) <= 600, f
    with pytest.raises(ValueError):
        load_fragment("../pyproject")


def test_order_and_determinism() -> None:
    a = PromptAssembler()
    parts = PromptParts(
        system_rules=[load_fragment(f) for f in FRAGMENTS[:2]],
        task_material=["任务：分类这张图", ImagePart(data_b64="AAAA")],
        capabilities=cands(2, 10),
        history=[Message(role="assistant", content="上一轮")],
    )
    out1, out2 = a.build(parts), a.build(parts)
    assert [m.model_dump() for m in out1.messages] == [m.model_dump() for m in out2.messages]
    assert out1.prompt_hash == out2.prompt_hash
    roles = [m.role for m in out1.messages]
    assert roles == ["system", "user", "assistant"]
    user = out1.messages[1].content
    assert isinstance(user, list) and isinstance(user[0], TextPart) and isinstance(user[1], ImagePart)
    assert isinstance(user[2], TextPart) and "可用能力目录" in user[2].text  # 能力描述在任务材料之后
    assert [d.level for d in out1.disclosed] == ["L1", "L1"]


def test_capability_degradation_order() -> None:
    items, degraded = pack_capabilities(cands(5), budget=900)
    # 低分的先从 L1 降为 L0
    assert degraded[0] == "cap4:L1→L0"
    assert sum(i.tokens for i in items) <= 900
    items, degraded = pack_capabilities(cands(5), budget=60)
    assert all(i.level == "L0" for i in items)
    assert any(d.endswith(":dropped") for d in degraded)
    assert [i.name for i in items] == sorted([i.name for i in items])  # 保留的是高分项
    items, _ = pack_capabilities(cands(5), budget=0)
    assert items == []


def test_task_material_never_squeezed() -> None:
    a = PromptAssembler(context_tokens=3000, output_reserve=500, capability_budget=1500)
    out = a.build(PromptParts(system_rules=["规则"], task_material=["材料" * 900], capabilities=cands(5)))
    assert out.tokens["task"] >= 1800 and out.capability_tokens <= 3000 - 500 - out.tokens["task"]
    with pytest.raises(PromptBudgetError):
        a.build(PromptParts(system_rules=["规则"], task_material=["材料" * 3000]))


def test_layered_context_assembler_caches_capabilities_per_run() -> None:
    calls: list[str] = []

    def provider(state):  # noqa: ANN001, ANN202
        calls.append(state["run_id"])
        return cands(1, 5)

    seen: list[int] = []
    asm = LayeredContextAssembler(
        "系统提示", capability_provider=provider, on_disclosed=lambda s, p: seen.append(len(p.disclosed))
    )
    st = initial_state("run_a", "问题？", {})
    m1 = asm.build(st)
    st["messages"] = [Message(role="assistant", content="x").model_dump()]
    m2 = asm.build(st)
    assert calls == ["run_a"] and seen == [1]
    assert m1[0].content == "系统提示" and "问题？" in str(m1[1].content) and "cap0" in str(m1[1].content)
    assert m2[:2] == m1 and m2[-1].role == "assistant"
