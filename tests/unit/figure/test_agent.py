import json

from langgraph.checkpoint.memory import InMemorySaver

from drugpaper.agents.figure.graph import FigureAgent
from drugpaper.facts.figure_models import Cell, TableGrid
from drugpaper.gateway.base import ChatResponse

CLASSIFICATION = {
    "image_type": "data_table",
    "legacy_type_id": 11,
    "has_chemical_structures": False,
    "has_markush_scaffold": False,
    "chart_types": [],
    "panel_count": 1,
    "evidence": ["aligned rows and printed values"],
    "confidence": 0.9,
    "needs_review": False,
}
GRID = TableGrid(
    orientation="properties_as_rows",
    subject_compound_label="26",
    header_rows=[[Cell(text="Property"), Cell(text="Value")]],
    rows=[[Cell(text="solubility (μM)"), Cell(text="50")]],
).model_dump()


class FigureFake:
    def __init__(self, mode="pass"):
        self.mode = mode
        self.calls = []
        self.checks = 0
        self.router = None

    async def aclose(self):
        pass

    async def chat(self, req, *, task="default"):
        self.calls.append((task, req))
        assert not req.tools
        if task == "visual_classify":
            payload = CLASSIFICATION
        elif task == "figure_structure":
            payload = json.loads(json.dumps(GRID))
            text = "\n".join(m.text() for m in req.messages)
            # This fixture PDF is blank; detector boxes are deliberate false positives.
            import re

            ids = {int(i) for i in re.findall(r'"mol_id":\s*(\d+)', text)}
            payload["molecule_inventory"] = [
                {
                    "mol_id": i,
                    "disposition": "excluded",
                    "evidence_text": "Blank fixture image; no chemical drawing in this box",
                }
                for i in sorted(ids)
            ]
        elif task == "remediate_decide":
            payload = {
                "action": "retranscribe_cells",
                "params": {"cells": ["rows[0][1]"]},
                "reason": "incorrect cell",
            }
        elif task == "figure_remediate_vision":
            payload = {
                "items": [
                    {
                        "dimension": "numeric",
                        "field_path": "rows[0][1].text",
                        "verdict": "incorrect",
                        "suggested_value": "50",
                        "reason": "reread",
                    }
                ]
            }
        elif task == "figure_self_check":
            self.checks += 1
            bad = self.mode == "fail" or self.mode == "fix" and self.checks == 1
            text = "\n".join(m.text() for m in req.messages)
            inputs, _ = json.JSONDecoder().raw_decode(text.split("Inputs:", 1)[1].lstrip())
            payload = {
                "items": [
                    {
                        "dimension": item["dimension"],
                        "field_path": item["field_path"],
                        "verdict": "incorrect" if bad and item["field_path"] == "rows[0][1]" else "correct",
                        "suggested_value": "50" if bad and item["field_path"] == "rows[0][1]" else None,
                        "reason": "visible value",
                    }
                    for item in inputs["checklist"]
                ]
            }
        else:
            raise AssertionError(task)
        return ChatResponse(content=json.dumps(payload))


async def test_exact_three_calls_no_tools_report_cache_and_owner(figure_env):
    sv, _, bundle, _ = figure_env
    fake = FigureFake()
    bundle.models = fake
    # Fingerprint requires the production router, while all chats use the fake model.
    from drugpaper.gateway.task_router import TaskRouter

    fake.router = TaskRouter.from_file(sv.settings.models_config_path)
    agent = FigureAgent(sv, InMemorySaver())
    report = await agent.run("doc_chem/r1/p000.g000")
    assert report.status == "ok", report.open_questions
    assert report.result_file and report.result_file["filename"].endswith("-result.json")
    delivered = json.loads(await sv.store_for("default").get(report.result_file["blob_ref"]))
    assert delivered["extraction"] == report.backfilled_extraction
    assert delivered["processing_stage"] == "reported"
    assert report.accounting.fixed_calls == 3 and report.accounting.total == 3
    assert [task for task, _ in fake.calls] == ["visual_classify", "figure_structure", "figure_self_check"]
    again = await FigureAgent(sv).run("doc_chem/r1/p000.g000")
    assert again.report_id == report.report_id and len(fake.calls) == 3
    import pytest

    with pytest.raises(PermissionError):
        await agent.run("doc_chem/r1/p000.g000", user_id="other")


async def test_targeted_retranscription_and_budget(figure_env):
    sv, _, bundle, _ = figure_env
    fake = FigureFake("fix")
    bundle.models = fake
    from drugpaper.gateway.task_router import TaskRouter

    fake.router = TaskRouter.from_file(sv.settings.models_config_path)
    report = await FigureAgent(sv).run("doc_chem/r1/p000.g000")
    assert report.status == "remediated", report.open_questions
    assert report.accounting.fixed_calls == 3 and report.accounting.remediation_calls == 3
    assert report.extraction["rows"][0][1]["source"] == "vlm_review"
    assert any("retranscribe_cells" in t.get("disclosed", []) for t in report.trace)


async def test_failed_remediation_stops_without_exceeding_four_calls(figure_env):
    sv, _, bundle, _ = figure_env
    fake = FigureFake("fail")
    bundle.models = fake
    from drugpaper.gateway.task_router import TaskRouter

    fake.router = TaskRouter.from_file(sv.settings.models_config_path)
    report = await FigureAgent(sv).run("doc_chem/r1/p000.g000")
    assert report.status == "needs_review", report.open_questions
    assert report.accounting.remediation_calls <= 4


async def test_undisclosed_action_and_remediation_timeout_preserve_review(figure_env):
    from drugpaper.gateway.base import ModelTimeout
    from drugpaper.gateway.task_router import TaskRouter

    sv, _, bundle, _ = figure_env
    for mode in ["undisclosed", "timeout"]:

        class UnsafeDecision(FigureFake):
            async def chat(self, req, *, task="default", decision_mode=mode):
                if task == "remediate_decide":
                    self.calls.append((task, req))
                    if decision_mode == "timeout":
                        raise ModelTimeout("decision deadline")
                    return ChatResponse(content='{"action":"run_arbitrary_code","params":{}}')
                return await super().chat(req, task=task)

        fake = UnsafeDecision("fail")
        fake.router = TaskRouter.from_file(sv.settings.models_config_path)
        bundle.models = fake
        report = await FigureAgent(sv).run("doc_chem/r1/p000.g000", force=True)
        assert report.status == "needs_review", report.open_questions
        assert report.accounting.remediation_calls == 1
        assert report.review.items and report.extraction["rows"]
        assert not any(t.get("step") == "run_arbitrary_code" for t in report.trace)


async def test_initial_selfcheck_failure_preserves_completed_extraction(figure_env):
    from drugpaper.gateway.base import SchemaViolation
    from drugpaper.gateway.task_router import TaskRouter

    class BrokenReview(FigureFake):
        async def chat(self, req, *, task="default"):
            if task == "figure_self_check":
                self.calls.append((task, req))
                raise SchemaViolation("missing review dimension", errors=["dimension is required"])
            return await super().chat(req, task=task)

    sv, _, bundle, _ = figure_env
    fake = BrokenReview()
    fake.router = TaskRouter.from_file(sv.settings.models_config_path)
    bundle.models = fake
    report = await FigureAgent(sv).run("doc_chem/r1/p000.g000")
    assert report.status == "needs_review"
    assert report.extraction["rows"] == GRID["rows"]
    assert report.accounting.fixed_calls == 3
    assert "dimension is required" in report.open_questions[0]
