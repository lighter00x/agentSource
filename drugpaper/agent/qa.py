"""文档问答 Agent 的装配。CLI、冒烟测试和阶段验证脚本共用这里的装配逻辑。

- `build_qa_agent`（P1）：layout.json + 3 个文档工具；
- `build_ir_qa_agent`（P2）：Document IR + doc_read / search 工具包；AgentRuntime 不改一行，只换数据源与工具注册。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from langgraph.checkpoint.base import BaseCheckpointSaver

from drugpaper.agent.config import AgentConfig
from drugpaper.agent.interfaces import Tracer
from drugpaper.agent.runtime import AgentRuntime
from drugpaper.agent.state import AgentRunResult, Budget
from drugpaper.agent.validators import CitationValidator
from drugpaper.capabilities.assembler import LayeredContextAssembler
from drugpaper.capabilities.index import CapabilityIndex
from drugpaper.capabilities.meta_tools import make_meta_tools
from drugpaper.capabilities.router import CapabilityRouter, DisclosureContext
from drugpaper.capabilities.toolset import DisclosureToolset
from drugpaper.docsrc.base import DocumentSource
from drugpaper.docsrc.ir_source import IrDocumentSource
from drugpaper.docsrc.layout_json import LayoutJsonSource
from drugpaper.domain.nodes import EvidenceRef
from drugpaper.gateway.base import ModelClient
from drugpaper.text.index import SearchBackend
from drugpaper.text.normalize import normalize_detail
from drugpaper.tools.builtin.doc_read import DOC_READ_TOOL_NAMES, make_doc_read_tools
from drugpaper.tools.builtin.layout_doc import LAYOUT_TOOL_NAMES, make_layout_tools
from drugpaper.tools.builtin.search import SEARCH_TOOL_NAMES, make_search_tools
from drugpaper.tools.protocol import ToolContext
from drugpaper.tools.registry import ToolRegistry

PROMPTS_DIR = Path(__file__).parent / "prompts"
IR_TOOL_NAMES = ["get_outline", *SEARCH_TOOL_NAMES, *[n for n in DOC_READ_TOOL_NAMES if n != "get_outline"]]


def load_prompt(name: str) -> str:
    return (PROMPTS_DIR / name).read_text(encoding="utf-8")


def new_ctx(document_id: str | None, run_id: str | None = None) -> ToolContext:
    return ToolContext(
        run_id=run_id or f"run_{uuid.uuid4().hex[:12]}",
        trace_id=f"tr_{uuid.uuid4().hex[:12]}",
        document_id=document_id,
    )


@dataclass
class QaAgent:
    runtime: AgentRuntime
    source: DocumentSource

    async def ask(self, question: str, *, run_id: str | None = None) -> AgentRunResult:
        result = await self.runtime.run(question, new_ctx(self.source.document_id, run_id))
        return self._with_evidence(result)

    async def resume(self, run_id: str) -> AgentRunResult:
        return self._with_evidence(await self.runtime.resume(new_ctx(self.source.document_id, run_id)))

    def _with_evidence(self, result: AgentRunResult) -> AgentRunResult:
        if isinstance(self.source, IrDocumentSource):
            result.evidence = [
                e.model_dump(mode="json") for e in resolve_evidence(self.source, result.answer)
            ]
        return result


def _quote_span(raw: str, quote: str) -> tuple[int, int] | None:
    """在 raw 中定位 quote：先精确查找，再在 norm 上查找并映射回 raw。"""
    i = raw.find(quote)
    if i >= 0:
        return (i, i + len(quote))
    d = normalize_detail(raw)
    j = d.norm.lower().find(" ".join(quote.split()).lower())
    if j >= 0:
        return d.map_span(j, j + len(" ".join(quote.split())))
    return None


def resolve_evidence(src: IrDocumentSource, answer: dict[str, Any]) -> list[EvidenceRef]:
    """把答案中的引用解析为 EvidenceRef：节点全局引用 + regions（页码与区域）+ quote 对应的 char_span。"""
    out: list[EvidenceRef] = []
    for c in answer.get("citations") or []:
        ref = c.get("ref")
        if not ref:
            continue
        try:
            n = src.node(ref)
        except KeyError:
            continue
        quote = c.get("quote")
        span = _quote_span(n.text_raw or "", quote) if quote and n.text_raw else None
        out.append(
            EvidenceRef(
                node_id=src.ref(n.local_id), char_span=span, regions=list(n.geometry.regions), role="primary"
            )
        )
    return out


def build_qa_agent(
    layout_path: Path | str,
    model: ModelClient,
    tracer: Tracer,
    *,
    budget: Budget | None = None,
) -> QaAgent:
    source = LayoutJsonSource.from_file(layout_path)
    registry = ToolRegistry(make_layout_tools(source))
    index = CapabilityIndex(None, registry)
    toolset = DisclosureToolset(registry, LAYOUT_TOOL_NAMES, index)
    for t in make_meta_tools(index, toolset):
        registry.register(t)
    config = AgentConfig(
        name="doc_qa",
        system_prompt=load_prompt("qa_system.md"),
        toolset=toolset,
        assembler=LayeredContextAssembler(load_prompt("qa_system.md")),
        validators=[CitationValidator(source)],
        budget=budget or Budget(),
    )
    return QaAgent(runtime=AgentRuntime(config, model, registry, tracer), source=source)


def build_ir_qa_agent(
    source: IrDocumentSource,
    model: ModelClient,
    tracer: Tracer,
    *,
    budget: Budget | None = None,
    search_backend: SearchBackend | None = None,
    checkpointer: BaseCheckpointSaver[Any] | None = None,
    skill_services: Any = None,
) -> QaAgent:
    registry = ToolRegistry([*make_doc_read_tools(source), *make_search_tools(source, search_backend)])
    if skill_services:
        for name in skill_services.tools.names():
            if name not in registry:
                registry.register(skill_services.tools.get(name))
    index = CapabilityIndex(skill_services.registry if skill_services else None, registry)
    if skill_services:
        index.entries.update(skill_services.index.entries)
    toolset = DisclosureToolset(registry, ["get_outline", "grep", "get_node"], index)
    for t in make_meta_tools(index, toolset):
        registry.register(t)
    config = AgentConfig(
        name="doc_qa_ir",
        system_prompt=load_prompt("qa_system_ir.md"),
        toolset=toolset,
        assembler=LayeredContextAssembler(
            load_prompt("qa_system_ir.md"),
            capability_provider=lambda state: (
                CapabilityRouter(index)
                .preselect(
                    DisclosureContext(
                        user_id=state["ctx"].get("user_id", "default"),
                        audience="chat",
                        intents=[state["user_input"]],
                        loaded=toolset.core + list(state.get("capability_state", {}).get("loaded", {})),
                    )
                )
                .candidates
            ),
        ),
        validators=[CitationValidator(source)],
        budget=budget or Budget(),
    )
    return QaAgent(
        runtime=AgentRuntime(config, model, registry, tracer, checkpointer=checkpointer), source=source
    )
