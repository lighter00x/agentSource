"""Single-figure deliveries use backfilled content and retain review/provenance."""

import copy
import json

import httpx
import pytest

from drugpaper.api.app import create_app
from drugpaper.facts.figure_result import build_result, persist_result
from drugpaper.persistence.models import FigureReportRow
from drugpaper.workbench.store import read, root_for, run_dir, write

REF = "doc_chem/r1/p000.g000"


def report():
    return {
        "report_id": "fr_result",
        "document_id": "doc_chem",
        "node_ref": REF,
        "run_id": "wd_result",
        "image_type": "scheme",
        "status": "needs_review",
        "extraction": {"unfilled": True},
        "backfilled_extraction": {
            "molecules": [{"reference": "26", "esmiles": "CCO", "formula": "C2H6O"}],
            "items": list(range(230)),
        },
        "checks": [{"code": "example", "severity": "warning"}],
        "open_questions": ["Member assignments still unknown"],
        "trace": [{"request": "debug-only material"}],
    }


def test_result_is_backfilled_complete_and_independent():
    original = report()
    snapshot = copy.deepcopy(original)
    result = build_result(original)
    assert result["extraction"] == original["backfilled_extraction"]
    assert len(result["extraction"]["items"]) == 230
    assert result["status"] == "needs_review" and result["quality"]["checks"]
    assert "trace" not in result and "unfilled" not in result["extraction"]
    assert result["run_id"] == original["run_id"] and result["report_id"] == original["report_id"]
    result["extraction"]["items"].clear()
    assert original == snapshot
    assert build_result(original, "backfilled")["status"] == "pending_review"
    with pytest.raises(ValueError):
        build_result({**original, "backfilled_extraction": {}})


async def test_reports_and_workbench_download_legacy_results_without_inference(figure_env):
    sv, _, _, _ = figure_env
    payload = report()
    async with sv.db.session() as s:
        s.add(
            FigureReportRow(
                id=payload["report_id"],
                user_id="default",
                document_id="doc_chem",
                node_ref=REF,
                mode="full",
                input_fingerprint="result",
                status="needs_review",
                payload=payload,
            )
        )
    root = root_for(sv, "doc_chem", 1)
    directory = run_dir(root, "wd_result")
    write(
        directory / "run.json",
        {"id": "wd_result", "node_ref": REF, "status": "needs_review", "created_at": "2026-11-21"},
    )
    write(directory / "states/report.json", {"report": payload})
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_app(sv)), base_url="http://test"
    ) as client:
        base = "/api/v1/documents/doc_chem"
        listed = (await client.get(base + "/figure-reports")).json()
        metadata = listed[0]["result_file"]
        stored = json.loads(await sv.store_for("default").get(metadata["blob_ref"]))
        assert stored["extraction"] == payload["backfilled_extraction"]
        for path in ["/figure-reports/fr_result/result", "/workbench/runs/wd_result/result"]:
            response = await client.get(base + path + "?download=true")
            assert response.status_code == 200
            assert response.json() == stored
            assert (
                response.headers["content-disposition"]
                == 'attachment; filename="p000.g000-wd_result-result.json"'
            )
            assert (await client.get(base + path)).json() == stored
        assert read(directory / "final-result.json") == stored
        assert (await client.get(base + "/figure-reports/missing/result")).status_code == 404
        assert (
            await client.get("/api/v1/documents/other/figure-reports/fr_result/result")
        ).status_code == 404
        pending = run_dir(root, "wd_pending")
        write(
            pending / "run.json",
            {"id": "wd_pending", "node_ref": REF, "status": "paused", "created_at": "2026-11-21"},
        )
        assert (await client.get(base + "/workbench/runs/wd_pending/result")).status_code == 409
        write(
            pending / "run.json",
            {"id": "wd_pending", "node_ref": REF, "status": "running", "created_at": "2026-11-21"},
        )
        assert (await client.get(base + "/workbench/runs/wd_pending/result")).status_code == 409
        write(
            pending / "run.json",
            {"id": "wd_pending", "node_ref": REF, "status": "paused", "created_at": "2026-11-21"},
        )
        write(pending / "states/post.json", {**payload, "report_id": None})
        data = (await client.get(base + "/workbench/runs/wd_pending/result")).json()
        assert data["processing_stage"] == "backfilled" and data["status"] == "pending_review"


async def test_result_blobs_are_immutable_versions(figure_env):
    sv, _, _, _ = figure_env
    a = report()
    meta_a, result_a = await persist_result(sv.store_for("default"), a)
    b = copy.deepcopy(a)
    b["run_id"] = "wd_next"
    b["backfilled_extraction"]["molecules"][0]["formula"] = "changed"
    meta_b, _ = await persist_result(sv.store_for("default"), b)
    assert meta_a["blob_ref"] != meta_b["blob_ref"] and meta_a["filename"] != meta_b["filename"]
    assert json.loads(await sv.store_for("default").get(meta_a["blob_ref"])) == result_a


@pytest.mark.parametrize("stop_after", ["post", None])
async def test_debug_run_saves_file_after_backfill_and_finishes_report(figure_env, monkeypatch, stop_after):
    from drugpaper.gateway.task_router import TaskRouter
    from drugpaper.workbench import runner
    from tests.unit.figure.test_agent import FigureFake

    sv, _, bundle, _ = figure_env
    fake = FigureFake()
    fake.router = TaskRouter.from_file(sv.settings.models_config_path)
    bundle.models = fake
    monkeypatch.setattr(runner.Services, "from_settings", lambda settings: sv)
    directory = run_dir(root_for(sv, "doc_chem", 1), "wd_export")
    write(
        directory / "run.json",
        {
            "id": "wd_export",
            "document_id": "doc_chem",
            "node_ref": REF,
            "revision": 1,
            "start": "classify",
            "stop_after": stop_after,
            "status": "queued",
            "code_hash": "stable",
            "steps": [],
            "created_at": "2026-11-21",
            "correction_version": 0,
        },
    )
    write(directory / "settings.json", sv.settings.model_dump(mode="json"))
    await runner.execute(directory)
    meta = read(directory / "run.json")
    assert meta["status"] == ("paused" if stop_after else "ok"), meta
    final = read(directory / "final-result.json")
    expected = "backfilled" if stop_after else "reported"
    assert final["processing_stage"] == expected
    assert final["status"] == ("pending_review" if stop_after else "ok")
    assert meta["result_file"]["processing_stage"] == expected
    assert json.loads(await sv.store_for("default").get(meta["result_file"]["blob_ref"])) == final
    assert read(directory / "states/post.json")["backfilled_extraction"] == final["extraction"]
