from __future__ import annotations

from fastapi.testclient import TestClient

from drugpaper.api.app import create_app
from drugpaper.config.settings import Settings
from drugpaper.services import Services


def test_skills_catalog_cards_versions_resources_and_admin_reload(tmp_path):
    sv = Services.from_settings(
        Settings(
            database_url=f"sqlite+aiosqlite:///{tmp_path}/api.db",
            blob_root=tmp_path / "blobs",
            skills_root=tmp_path / "skills",
            skills_watch_enabled=False,
            skills_admin_token="test-admin",
            redis_url="",
        )
    )
    with TestClient(create_app(sv)) as client:
        base = "/api/v1"
        rows = client.get(base + "/skills").json()
        from drugpaper.skills.bootstrap import BUILTIN_ROOT

        expected = {p.name for p in BUILTIN_ROOT.iterdir() if (p / "manifest.yaml").exists()} - {
            "figure_describe"
        }
        assert {s["name"] for s in rows} == expected
        assert client.get(base + "/skills/visual_classify/card").json()["tokens"]["L0"] <= 40
        assert client.get(base + "/skills/visual_classify/versions").json()[0]["status"] == "active"
        assert client.get(base + "/skills/visual_classify/resources").json()["resources"]
        assert client.get(base + "/skills/figure_describe/card").status_code == 404
        assert all(c["toolkit"] == "chem" for c in client.get(base + "/skills/catalog?toolkit=chem").json())
        assert client.get(base + "/skills/visual_classify/card?audience=codegen").status_code == 404
        assert client.post(base + "/skills:reload").status_code == 403
        assert (
            client.post(base + "/skills:reload", headers={"X-Admin-Token": "test-admin"}).status_code == 200
        )
        client.portal.call(sv.aclose)


async def test_disclosures_persist_per_model_call_and_skill_tools_are_optional(tmp_path):
    from sqlalchemy import select

    from drugpaper.agent.config import AgentConfig
    from drugpaper.agent.runtime import AgentRuntime
    from drugpaper.capabilities.meta_tools import make_meta_tools
    from drugpaper.capabilities.toolset import DisclosureToolset
    from drugpaper.persistence.models import CapabilityDisclosureRow
    from drugpaper.tools.protocol import ToolContext
    from tests.fakes.fake_llm import FakeLLM, ListTracer, call, submit, tools

    sv = Services.from_settings(
        Settings(
            database_url=f"sqlite+aiosqlite:///{tmp_path}/test.db",
            blob_root=tmp_path / "blobs",
            skills_root=tmp_path / "skills",
            redis_url="",
        )
    )
    await sv.db.create_all()
    try:
        bundle = await sv.skills()
        toolset = DisclosureToolset(bundle.tools, [], bundle.index)
        for t in make_meta_tools(bundle.index, toolset):
            bundle.tools.register(t)
        llm = FakeLLM([tools(call("find_capabilities", {"query": "分子拼接"})), submit("done")])
        agent = AgentRuntime(
            AgentConfig(name="test", system_prompt="test", toolset=toolset), llm, bundle.tools, ListTracer()
        )
        result = await agent.run("拼接", ToolContext(run_id="run_disclosure_db", trace_id="tr"))
        assert result.stop_reason == "answered"
        assert "markush.substitute" not in llm.tool_names(0)
        async with sv.db.session() as s:
            rows = list((await s.execute(select(CapabilityDisclosureRow))).scalars())
            assert len(rows) == 2 and rows[0].total_tokens > 0
            assert rows[0].used == ["find_capabilities"]
    finally:
        await sv.aclose()
