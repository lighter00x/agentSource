import asyncio

from drugpaper.agents.coordinator.dispatch import Coordinator
from drugpaper.agents.figure.graph import FigureAgent
from drugpaper.agents.figure.report import FigureReport
from drugpaper.domain.geometry import BBox, Geometry, Region
from drugpaper.domain.nodes import DocumentNode, IRDocument, PageInfo
from drugpaper.gateway.base import ChatResponse
from drugpaper.gateway.task_router import TaskRouter
from drugpaper.orchestration.figure_pipeline import FigurePipeline
from drugpaper.persistence.models import Document
from drugpaper.persistence.repos.nodes import save_ir
from tests.unit.figure.test_agent import FigureFake


async def test_coordinator_page_priority_and_six_agent_concurrency(figure_env, monkeypatch):
    sv, _, _, _ = figure_env
    nodes = [
        DocumentNode(
            document_id="doc_chem",
            revision=1,
            local_id=f"p000.g{i:03d}",
            node_type="scheme" if i == 8 else "figure",
            geometry=Geometry(regions=[Region(page=0, bbox=BBox(x0=0, y0=0, x1=1, y1=1))]),
        )
        for i in range(9)
    ]
    async with sv.db.session() as session:
        await save_ir(
            session,
            IRDocument(
                document_id="doc_chem", pages=[PageInfo(page=0, width_pt=100, height_pt=100)], nodes=nodes
            ),
        )
    active = peak = 0
    order = []

    async def run(self, ref, **kwargs):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        order.append(ref)
        await asyncio.sleep(0.01)
        active -= 1
        return FigureReport(
            report_id="report-" + ref, document_id="doc_chem", node_ref=ref, status="ok", run_id="fake"
        )

    monkeypatch.setattr(FigureAgent, "run", run)
    result = await Coordinator(sv).run("doc_chem")
    assert len(result["reports"]) == 9 and peak == 6
    assert order[0].endswith("p000.g008")
    assert result["status"] == "completed" and result["reconcile"]["questions"] == {}


async def test_send_pipeline_reduces_reports_and_persists_quality(figure_env):
    sv, _, bundle, _ = figure_env

    class PipelineFake(FigureFake):
        async def chat(self, req, *, task="default"):
            if task == "figure_query":
                self.calls.append((task, req))
                return ChatResponse(content='{"answers":[{"answer":"unclear","evidence_text":"50"}]}')
            return await super().chat(req, task=task)

    fake = PipelineFake()
    fake.router = TaskRouter.from_file(sv.settings.models_config_path)
    bundle.models = fake
    result = await FigurePipeline(sv).run("doc_chem")
    assert result["status"] == "completed" and len(result["reports"]) == 1
    assert result["blackboard_v1"]["version"] == 1 and result["blackboard_v2"]["version"] == 2
    assert result["calls"] == len(fake.calls)
    async with sv.db.session() as session:
        doc = await session.get(Document, "doc_chem")
        assert doc.quality_report["figure_agents"]["figure_count"] == 1
        assert doc.quality_report["figure_agents"]["model_calls"] == result["calls"]
