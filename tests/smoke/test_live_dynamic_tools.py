"""配置的真实文本模型完成发现、加载和调用；仅 --smoke 时运行。"""

from pathlib import Path

import pytest
from sqlalchemy import select

from drugpaper.agent.config import AgentConfig
from drugpaper.agent.runtime import AgentRuntime
from drugpaper.agent.state import Budget
from drugpaper.capabilities.meta_tools import make_meta_tools
from drugpaper.capabilities.toolset import DisclosureToolset
from drugpaper.config.settings import Settings
from drugpaper.persistence.models import CapabilityDisclosureRow
from drugpaper.services import Services
from drugpaper.tools.protocol import ToolContext
from drugpaper.tracing.jsonl import JsonlTracer

pytestmark = pytest.mark.smoke


async def test_real_discover_load_and_substitute(tmp_path: Path) -> None:
    sv = Services.from_settings(
        Settings(
            database_url=f"sqlite+aiosqlite:///{tmp_path / 'live.db'}",
            redis_url="",
            skills_root=tmp_path / "skills",
            skills_watch_enabled=False,
            blob_root=tmp_path / "blobs",
            runs_dir=tmp_path / "runs",
        )
    )
    await sv.db.create_all()
    try:
        bundle = await sv.skills()
        toolset = DisclosureToolset(bundle.tools, [], bundle.index)
        for tool in make_meta_tools(bundle.index, toolset):
            bundle.tools.replace(tool)
        agent = AgentRuntime(
            AgentConfig(
                name="live_dynamic_tools",
                task="chat",
                toolset=toolset,
                system_prompt="先发现能力，再加载，下一轮实际调用工具。最终调用 submit_answer。",
                budget=Budget(max_steps=8, max_model_calls=10, max_wall_time_s=180),
                max_tokens=4096,
            ),
            bundle.models,
            bundle.tools,
            JsonlTracer(tmp_path / "runs"),
        )
        ctx = ToolContext(run_id="live_dynamic_tools", trace_id="live_dynamic_tools")
        result = await agent.run(
            "先 find_capabilities 查找 markush.substitute，再 load_capability，下一轮调用，"
            'caption=C1CCCCC1*<sep><a>6:R</a>，definitions={"R":"Me"}，max_outputs=64。'
            "报告工具返回的拼接 SMILES。",
            ctx,
        )
        assert result.stop_reason == "answered", result.error
        assert "CC1CCCCC1" in result.answer["answer"]
        async with sv.db.session() as session:
            rows = list((await session.execute(select(CapabilityDisclosureRow))).scalars())
            used = [name for row in rows for name in row.used]
            assert {"find_capabilities", "load_capability", "markush.substitute"} <= set(used)
            assert len(rows) == result.model_calls
    finally:
        await sv.aclose()
