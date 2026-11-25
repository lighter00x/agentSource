from __future__ import annotations

import httpx

from drugpaper.api.app import create_app
from drugpaper.api.workbench import required_stage
from drugpaper.persistence.models import Document
from drugpaper.workbench import runner
from drugpaper.workbench.store import read, root_for, run_dir, write
from tests.unit.figure.test_agent import FigureFake

REF = "doc_chem/r1/p000.g000"


async def test_workbench_scope_layout_corrections_and_local_files(figure_env):
    sv, _, _, _ = figure_env
    app = create_app(sv)
    base = "/api/v1/documents/doc_chem/workbench"
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        overview = (await client.get(base)).json()
        assert overview["figures"][0]["node_ref"] == REF
        layout = (await client.get(base + "/layout?page=0")).json()
        assert not any(
            n["type"] in {"figure_part", "caption", "footnote", "molecule"} for n in layout["nodes"]
        )
        assert (
            await client.get(base + "/molecules", params={"node_ref": "other/r1/p000.g000"})
        ).status_code == 409
        path = "drugpaper/skills/builtin/visual_classify/SKILL.md"
        original = (await client.get(base + "/files", params={"node_ref": REF, "path": path})).json()
        payload = {
            "node_ref": REF,
            "path": path,
            "content": original["content"] + "\nLocal test\n",
            "expected_version": 0,
        }
        assert (await client.put(base + "/files", json=payload)).status_code == 200
        assert (await client.put(base + "/files", json=payload)).status_code == 409
        assert (
            await client.get(base + "/files", params={"node_ref": REF, "path": "../../.env"})
        ).status_code == 422
        assert (
            await client.post(base + "/publish", json={"node_ref": REF, "expected_version": 1})
        ).status_code == 409
        saved = (await client.get(base + "/files", params={"node_ref": REF, "path": path})).json()
        assert saved["content"].endswith("Local test\n") and saved["baseline"] == original["baseline"]
        box = {
            "node_ref": REF,
            "kind": "box",
            "bbox": {"x0": 0.1, "y0": 0.1, "x1": 0.3, "y1": 0.4},
            "expected_version": 0,
        }
        result = await client.post(base + "/corrections", json=box)
        assert result.status_code == 200, result.text
        mols = (await client.get(base + "/molecules", params={"node_ref": REF})).json()
        mol = mols["molecules"][0]
        assert mol["manual_box"] and mol["geometry"]["regions"][0]["page"] == 0
        smiles = {
            "node_ref": REF,
            "kind": "smiles",
            "molecule_ref": mol["node_ref"],
            "smiles": "CCO",
            "expected_version": 1,
        }
        result = await client.post(base + "/corrections", json=smiles)
        assert result.status_code == 200, result.text
        mols = (await client.get(base + "/molecules", params={"node_ref": REF})).json()
        assert mols["molecules"][0]["structure"]["formula"] == "C2H6O"
        assert mols["molecules"][0]["structure"]["source"] == "manual"
        assert mols["history"][-1]["stale_from"] == "post"
        bad = {**smiles, "smiles": "not a molecule", "expected_version": 2}
        assert (await client.post(base + "/corrections", json=bad)).status_code == 422
        result = await client.post(
            base + "/corrections",
            json={"node_ref": REF, "kind": "restore", "molecule_ref": mol["node_ref"], "expected_version": 2},
        )
        assert result.status_code == 200, result.text
        mols = (await client.get(base + "/molecules", params={"node_ref": REF})).json()
        assert mols["molecules"][0]["status"] == "removed"
        async with sv.db.session() as s:
            doc = await s.get(Document, "doc_chem")
            doc.deleted_at = __import__("datetime").datetime.now(__import__("datetime").UTC)
        assert (await client.get(base)).status_code == 404


async def test_debug_pause_then_structure_reuses_molecules_and_keeps_history(figure_env, monkeypatch):
    sv, backend, bundle, _ = figure_env
    fake = FigureFake()
    from drugpaper.gateway.task_router import TaskRouter

    fake.router = TaskRouter.from_file(sv.settings.models_config_path)
    bundle.models = fake
    monkeypatch.setattr(runner.Services, "from_settings", lambda settings: sv)
    root = root_for(sv, "doc_chem", 1)
    first = run_dir(root, "wd_first")
    meta = {
        "id": "wd_first",
        "document_id": "doc_chem",
        "node_ref": REF,
        "revision": 1,
        "start": "classify",
        "stop_after": "recognize",
        "status": "queued",
        "code_hash": "stable",
        "steps": [],
        "created_at": "2026-11-20",
        "correction_version": 0,
    }
    write(first / "run.json", meta)
    write(first / "settings.json", sv.settings.model_dump(mode="json"))
    await runner.execute(first)
    after = read(first / "run.json")
    assert after["status"] == "paused", after
    assert after["next_stage"] == "som"
    assert read(first / "states/recognize.json")["chain"]
    assert [t for t, _ in fake.calls] == ["visual_classify"]
    state = read(first / "states/recognize.json")
    second = run_dir(root, "wd_second")
    write(
        second / "run.json",
        {
            **meta,
            "id": "wd_second",
            "start": "som",
            "stop_after": None,
            "initial_state": state,
            "source_run": "wd_first",
        },
    )
    write(second / "settings.json", sv.settings.model_dump(mode="json"))
    await runner.execute(second)
    after = read(second / "run.json")
    assert after["status"] in {"ok", "needs_review"}, after
    assert not any(s["stage"] in {"detect", "recognize"} for s in after["steps"])
    assert [t for t, _ in fake.calls] == ["visual_classify", "figure_structure", "figure_self_check"]
    assert read(first / "run.json")["status"] == "paused"
    third = run_dir(root, "wd_third")
    write(
        third / "run.json",
        {
            **meta,
            "id": "wd_third",
            "start": "structure",
            "stop_after": "structure",
            "initial_state": read(second / "states/som.json"),
        },
    )
    write(third / "settings.json", sv.settings.model_dump(mode="json"))
    await runner.execute(third)
    assert read(third / "run.json")["status"] == "paused"
    assert len(fake.calls) <= 4  # May reuse the unchanged extraction Artifact.
    assert list((third / "events").glob("*.json"))


def test_code_dependency_stage_and_redaction():
    assert required_stage("drugpaper/skills/builtin/table_extract/prompts/task.md") == "structure"
    assert required_stage("prompts/common/member_assignment_fields.md") == "structure"
    assert required_stage("drugpaper/tools/builtin/molecule_detection.py") == "classify"
    assert runner.clean({"api_key": "hidden", "usage": {"input_tokens": 10}, "error": "Bearer secret"}) == {
        "api_key": "[redacted]",
        "usage": {"input_tokens": 10},
        "error": "[redacted]",
    }


async def test_isolated_process_executes_local_python_and_rejects_stale_upstream(figure_env):
    import asyncio

    import yaml

    from tests.unit.figure.test_agent import CLASSIFICATION

    sv, _, _, _ = figure_env
    app = create_app(sv)
    base = "/api/v1/documents/doc_chem/workbench"
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        refpath = "drugpaper/skills/builtin/visual_classify/"
        original = (
            await client.get(base + "/files", params={"node_ref": REF, "path": refpath + "manifest.yaml"})
        ).json()
        manifest = yaml.safe_load(original["content"])
        manifest.update(kind="python", entry="scripts/local.py:run")
        for version, path, content in [
            (0, refpath + "manifest.yaml", yaml.safe_dump(manifest)),
            (
                1,
                refpath + "scripts/local.py",
                'from drugpaper.skills.protocol import SkillResult, ArtifactDraft\nasync def run(inputs, ctx):\n    return SkillResult(artifacts=[ArtifactDraft(artifact_type="visual_classify", payload='
                + repr(CLASSIFICATION)
                + ")])\n",
            ),
        ]:
            response = await client.put(
                base + "/files",
                json={"node_ref": REF, "path": path, "content": content, "expected_version": version},
            )
            assert response.status_code == 200, response.text
        result = await client.post(base + "/runs", json={"node_ref": REF, "stop_after": "classify"})
        assert result.status_code == 202, result.text
        rid = result.json()["id"]
        for _ in range(300):
            detail = (await client.get(base + "/runs/" + rid)).json()
            if detail["run"]["status"] not in {"queued", "running"}:
                break
            await asyncio.sleep(0.1)
        assert detail["run"]["status"] == "paused", detail
        assert detail["states"]["classify"]["classification"]["image_type"] == "data_table"
        assert detail["states"]["classify"]["accounting"]["fixed_calls"] == 0
        assert detail["run"]["draft_version"] == 2
        # Editing classification invalidates inherited classification before plan.
        await client.put(
            base + "/files",
            json={
                "node_ref": REF,
                "path": refpath + "scripts/local.py",
                "content": 'async def run(inputs, ctx):\n    raise RuntimeError("changed")\n',
                "expected_version": 2,
            },
        )
        rejected = await client.post(
            base + "/runs", json={"node_ref": REF, "source_run": rid, "start": "plan", "stop_after": "plan"}
        )
        assert rejected.status_code == 409, rejected.text
        active = await (await sv.skills()).registry.resolve("visual_classify")
        assert active.manifest.kind == "prompt"  # Local subprocess never activates a global Skill.


async def test_auto_isolates_figure_failures_and_manual_upload_only_parses(figure_env, monkeypatch):
    from types import SimpleNamespace

    from drugpaper.api import documents, workbench
    from drugpaper.domain.geometry import BBox, Geometry, Region
    from drugpaper.domain.nodes import DocumentNode
    from drugpaper.persistence.repos.nodes import node_to_row

    sv, _, _, _ = figure_env
    async with sv.db.session() as s:
        second = DocumentNode(
            document_id="doc_chem",
            revision=1,
            local_id="p001.g001",
            node_type="figure",
            geometry=Geometry(regions=[Region(page=1, bbox=BBox(x0=0.1, y0=0.1, x1=0.9, y1=0.9))]),
        )
        s.add(
            __import__("drugpaper.persistence.models", fromlist=["NodeRow"]).NodeRow(
                **node_to_row(second, "default")
            )
        )
    attempts = []

    async def fake_launch(sv, s, docid, body):
        attempts.append(body.node_ref)
        if len(attempts) == 1:
            raise RuntimeError("one figure failed")
        return {"id": "wd_second", "status": "queued"}

    monkeypatch.setattr(workbench, "launch", fake_launch)
    calls = []

    async def parse_only(self, **kwargs):
        calls.append(kwargs)
        return SimpleNamespace(document_id="doc_chem", status="ready")

    monkeypatch.setattr(documents.DocumentPipeline, "run", parse_only)
    await documents._run_workbench_pipeline(sv, b"%PDF-local", "local.pdf", "manual")
    assert len(calls) == 1 and not attempts
    await documents._run_workbench_pipeline(sv, b"%PDF-local", "local.pdf", "auto")
    assert len(calls) == 2 and len(attempts) == 2
    assert attempts[0] != attempts[1]
    async with sv.db.session() as s:
        doc = await s.get(Document, "doc_chem")
        assert doc.meta["workbench_mode"] == "auto"


async def test_single_recognition_keeps_manual_smiles_as_effective_value(figure_env, monkeypatch):
    from drugpaper.persistence.models import NodeRow

    sv, _, _, run = figure_env
    await run("molecule_detect")
    async with sv.db.session() as s:
        from sqlalchemy import select

        row = (await s.execute(select(NodeRow).where(NodeRow.node_type == "molecule"))).scalars().first()
        ref = f"doc_chem/r1/{row.local_id}"
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_app(sv)), base_url="http://test"
    ) as client:
        response = await client.post(
            "/api/v1/documents/doc_chem/workbench/corrections",
            json={
                "node_ref": REF,
                "kind": "smiles",
                "molecule_ref": ref,
                "smiles": "CCN",
                "expected_version": 0,
            },
        )
        assert response.status_code == 200, response.text
    root = root_for(sv, "doc_chem", 1)
    directory = run_dir(root, "wd_recognize")
    write(directory / "settings.json", sv.settings.model_dump(mode="json"))
    write(
        directory / "run.json",
        {
            "id": "wd_recognize",
            "document_id": "doc_chem",
            "revision": 1,
            "node_ref": REF,
            "start": "recognize",
            "stop_after": "recognize",
            "operation": "recognize_one",
            "molecule_ref": ref,
            "status": "queued",
            "code_hash": "test",
            "initial_state": {
                "document_id": "doc_chem",
                "node_ref": REF,
                "user_id": "default",
                "blackboard": {},
                "chain": ["molecule_recognize"],
                "round": 0,
                "accounting": {},
                "trace": [],
                "artifact_ids": [],
            },
        },
    )
    monkeypatch.setattr(runner.Services, "from_settings", lambda settings: sv)
    await runner.execute(directory)
    assert read(directory / "run.json")["status"] == "completed"
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_app(sv)), base_url="http://test"
    ) as client:
        result = (
            await client.get("/api/v1/documents/doc_chem/workbench/molecules", params={"node_ref": REF})
        ).json()
        selected = next(m for m in result["molecules"] if m["node_ref"] == ref)
        assert selected["structure"]["canonical_smiles"] == "CCN"
        assert selected["manual_smiles"]
    events = [read(p) for p in (directory / "events").glob("*.json")]
    assert any(e["kind"] == "tool_request" and e["data"]["name"] == "molparser.recognize" for e in events)
    assert any(e["kind"] == "manual_reuse" for e in events)


async def test_interrupted_process_exposes_sanitized_startup_error(figure_env):
    sv, _, _, _ = figure_env
    root = root_for(sv, "doc_chem", 1)
    directory = run_dir(root, "wd_dead")
    write(
        directory / "run.json",
        {
            "id": "wd_dead",
            "node_ref": REF,
            "status": "running",
            "pid": 2147483647,
            "created_at": "2026-11-20T00:00:00Z",
            "steps": [],
        },
    )
    (directory / "process.log").write_text(
        "ModuleNotFoundError: local tool\nBearer private-token\npostgresql://dev:secretpassword@localhost/db\n"
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_app(sv)), base_url="http://test"
    ) as client:
        result = await client.get("/api/v1/documents/doc_chem/workbench/runs/wd_dead")
        assert result.status_code == 200, result.text
        data = result.json()
        assert data["run"]["status"] == "interrupted"
        assert "ModuleNotFoundError" in data["process_error"]
        assert "private-token" not in result.text and "secretpassword" not in result.text


def test_retry_policy_does_not_invalidate_completed_model_results():
    from drugpaper.api.workbench import model_result_config

    old = {"candidates": [{"model_name": "vision", "timeout_s": 180}]}
    updated = {
        "candidates": [
            {"model_name": "vision", "timeout_s": 240, "retry_timeout_multiplier": 2, "retry_base_delay_s": 5}
        ]
    }
    assert model_result_config(old) == model_result_config(updated)
    other = {"candidates": [{"model_name": "other"}]}
    assert model_result_config(old) != model_result_config(other)


async def test_reviewed_esmiles_preserves_sites_and_invalidates_only_post(figure_env):
    sv, _, _, run = figure_env
    await run("molecule_detect")
    app = create_app(sv)
    base = "/api/v1/documents/doc_chem/workbench"
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        data = (await client.get(base + "/molecules", params={"node_ref": REF})).json()
        node = data["molecules"][0]["node_ref"]
        esmiles = "*c1ccccc1<sep><a>0:R</a>"
        response = await client.post(
            base + "/corrections",
            json={
                "node_ref": REF,
                "kind": "smiles",
                "molecule_ref": node,
                "smiles": esmiles,
                "reason": "原图核对明确R连接位点",
                "expected_version": data["version"],
            },
        )
        assert response.status_code == 200, response.text
        assert response.json()["stale_from"] == "post"
        updated = (await client.get(base + "/molecules", params={"node_ref": REF})).json()
        item = next(x for x in updated["molecules"] if x["node_ref"] == node)
        assert item["structure"]["e_smiles_raw"] == esmiles
        assert updated["history"][-1]["reason"] == "原图核对明确R连接位点"
    assert required_stage("drugpaper/chem/product_candidates.py") == "post"
    assert required_stage("drugpaper/chem/assembly_compat.py") == "post"
    assert required_stage("drugpaper/skills/builtin/mol_backfill/scripts/main.py") == "post"
