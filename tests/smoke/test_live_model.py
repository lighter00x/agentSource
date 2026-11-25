"""真实模型端点冒烟测试（默认跳过）：`make smoke` 或 `pytest tests/smoke --smoke`。

需要 .env 中的 LLM_BASE_URL / LLM_API_KEY / LLM_MODEL。
优先使用真实样例论文 tests/fixtures/jmedchem_2c00949/*_layout.json，缺失时退回合成数据。
"""

from __future__ import annotations

import pytest

from drugpaper.agent.qa import build_qa_agent
from drugpaper.agent.state import AgentRunResult
from drugpaper.config.models_file import ModelsConfigError, load_default_model
from drugpaper.config.settings import get_settings
from drugpaper.gateway.base import ChatRequest, Message
from drugpaper.gateway.openai_compat import OpenAICompatClient
from drugpaper.tracing.jsonl import JsonlTracer, read_events
from tests.conftest import MINI_LAYOUT, real_layout_path

pytestmark = pytest.mark.smoke

QUESTIONS = [
    ("UCM-1306 对应论文中的哪个化合物编号？", ["26"]),
    ("这篇论文的研究对象是什么受体？", ["D1", "D 1", "D₁"]),
]


@pytest.fixture
def client() -> OpenAICompatClient:  # 函数级：每个测试有独立事件循环，连接池不跨循环复用
    try:
        return OpenAICompatClient(load_default_model(get_settings()))
    except ModelsConfigError as e:
        pytest.skip(f"模型端点未配置：{e}")


async def test_hello(client: OpenAICompatClient) -> None:
    limit = 1024 if client.model.api == "responses" else 64
    resp = await client.chat(ChatRequest(messages=[Message(role="user", content="你好")], max_tokens=limit))
    print(f"\nmodel={resp.model} latency={resp.latency_ms}ms tokens={resp.usage}")
    assert resp.content


def _valid_citations(result: AgentRunResult) -> list[str]:
    seen = set(result.seen_refs)
    return [c["ref"] for c in result.answer.get("citations", []) if c.get("ref") in seen]


@pytest.mark.parametrize(("question", "expected"), QUESTIONS)
async def test_qa(client: OpenAICompatClient, question: str, expected: list[str]) -> None:
    layout = real_layout_path() or MINI_LAYOUT
    tracer = JsonlTracer(get_settings().runs_dir)
    agent = build_qa_agent(layout, client, tracer)
    result = await agent.ask(question)
    print(
        f"\n[{layout.name}] {question}\n→ {result.answer}\n{result.stop_reason} steps={result.steps} {result.elapsed_ms}ms"
    )
    assert any(e in result.answer["answer"] for e in expected)
    assert _valid_citations(result), "至少需要 1 个有效引用"
    assert result.steps <= 5
    kinds = [e["kind"] for e in read_events(tracer.path_for(result.run_id))]
    assert kinds[0] == "run_start" and kinds[-1] == "run_end" and "model_call" in kinds
