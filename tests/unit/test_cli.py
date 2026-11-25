from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

import drugpaper.cli as cli
from drugpaper.config.models_file import MissingEnvVarError, ResolvedModel
from drugpaper.config.settings import Settings
from tests.conftest import MINI_LAYOUT
from tests.fakes.fake_llm import FakeLLM, call, submit, tools

runner = CliRunner()


class ClosableFake(FakeLLM):
    async def aclose(self) -> None:
        return None


@pytest.fixture
def patched(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> dict[str, Any]:
    holder: dict[str, Any] = {"runs": tmp_path / "runs"}
    monkeypatch.setattr(cli, "get_settings", lambda: Settings(runs_dir=holder["runs"]))
    monkeypatch.setattr(
        cli, "load_default_model", lambda s: ResolvedModel(base_url="http://x", api_key="k", model_name="m")
    )

    def factory(_: Any) -> ClosableFake:
        fake = ClosableFake(
            [
                tools(call("grep", {"pattern": "UCM-1306"})),
                submit("UCM-1306 是化合物 26。", [{"ref": "p000.b007", "quote": "UCM-1306 (26)"}]),
            ]
        )
        holder["fake"] = fake
        return fake

    monkeypatch.setattr(cli, "OpenAICompatClient", factory)
    return holder


def test_help() -> None:
    res = runner.invoke(cli.app, ["ask", "--help"])
    assert res.exit_code == 0
    for opt in ["--max-steps", "--max-model-calls", "--verbose", "--json"]:
        assert opt in res.output


def test_molecules_command_runs_force_and_closes_services(monkeypatch):
    from types import SimpleNamespace

    holder = {}

    class Service:
        db = SimpleNamespace(is_postgres=True)

        async def aclose(self):
            holder["closed"] = True

    class Pipeline:
        def __init__(self, services):
            assert isinstance(services, Service)

        async def run(self, document_id, *, force):
            holder["args"] = (document_id, force)
            return {"runs": [], "document_id": document_id}

    monkeypatch.setattr(cli, "get_settings", lambda: Settings(_env_file=None))
    monkeypatch.setattr("drugpaper.services.Services.from_settings", lambda settings: Service())
    monkeypatch.setattr("drugpaper.orchestration.molecule_pipeline.MoleculePipeline", Pipeline)
    result = runner.invoke(cli.app, ["molecules", "--doc", "doc_test", "--force"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["document_id"] == "doc_test"
    assert holder == {"args": ("doc_test", True), "closed": True}


def test_ask_json(patched: dict[str, Any]) -> None:
    res = runner.invoke(cli.app, ["ask", str(MINI_LAYOUT), "UCM-1306 是哪个化合物？", "--json"])
    assert res.exit_code == 0, res.output
    data = json.loads(res.output)
    assert data["stop_reason"] == "answered"
    assert data["answer"]["citations"][0]["ref"] == "p000.b007"
    assert (patched["runs"] / f"{data['run_id']}.jsonl").exists()


def test_ask_pretty_verbose(patched: dict[str, Any]) -> None:
    res = runner.invoke(
        cli.app, ["ask", str(MINI_LAYOUT), "UCM-1306 是哪个化合物？", "-v", "--max-steps", "3"]
    )
    assert res.exit_code == 0, res.output
    assert "化合物 26" in res.output and "p000.b007" in res.output and "stop_reason=answered" in res.output


def test_ask_config_error(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(cli, "get_settings", lambda: Settings(runs_dir=tmp_path))

    def boom(_: Any) -> ResolvedModel:
        raise MissingEnvVarError("LLM_BASE_URL", "providers.default_llm.base_url")

    monkeypatch.setattr(cli, "load_default_model", boom)
    res = runner.invoke(cli.app, ["ask", str(MINI_LAYOUT), "q"])
    assert res.exit_code == 2
    assert "LLM_BASE_URL" in res.output


# ---------------------------------------------------------------- P2：ingest --offline 与 ask --doc

from tests.unit.ir.conftest import FIXTURE_DIR, needs_real  # noqa: E402


@needs_real
def test_ingest_offline_then_ask_doc(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    settings = Settings(
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'cli.db'}",
        blob_root=tmp_path / "blobs",
        runs_dir=tmp_path / "runs",
        render_dpi=36,
    )
    monkeypatch.setattr(cli, "get_settings", lambda: settings)
    res = runner.invoke(cli.app, ["ingest", "--offline", str(FIXTURE_DIR)])
    assert res.exit_code == 0, res.output
    doc_id = json.loads(res.output[res.output.index("{") :])["document_id"]

    monkeypatch.setattr(
        cli, "load_default_model", lambda s: ResolvedModel(base_url="http://x", api_key="k", model_name="m")
    )
    ref = f"{doc_id}/r1/p007.g001.c0"
    monkeypatch.setattr(
        cli,
        "OpenAICompatClient",
        lambda _: ClosableFake(
            [
                tools(call("grep", {"pattern": "Table 3", "node_types": ["caption"]})),
                submit(
                    "ADMET Profile for Compound 26", [{"ref": ref, "quote": "ADMET Profile for Compound 26"}]
                ),
            ]
        ),
    )
    out = runner.invoke(cli.app, ["ask", "--doc", doc_id, "Table 3 的标题是什么？", "--json"])
    assert out.exit_code == 0, out.output
    data = json.loads(out.output)
    assert data["stop_reason"] == "answered"
    assert data["evidence"][0]["node_id"] == ref and data["evidence"][0]["regions"][0]["page"] == 7
    pretty = runner.invoke(cli.app, ["ask", "--doc", doc_id, "Table 3 的标题是什么？"])
    assert pretty.exit_code == 0 and "p007.g001.c0" in pretty.output
    bad = runner.invoke(cli.app, ["ask", "--doc", doc_id, "a", "b"])
    assert bad.exit_code != 0


def test_text_facts_command_runs_and_propagates_failure(monkeypatch):
    from types import SimpleNamespace

    holder = {}

    class Service:
        db = SimpleNamespace(is_postgres=True)

        async def aclose(self):
            holder["closed"] = True

    class Pipeline:
        def __init__(self, services):
            assert isinstance(services, Service)

        async def run(self, document_id, *, force):
            holder["args"] = (document_id, force)
            return {"status": "failed", "runs": [], "document_id": document_id}

    monkeypatch.setattr(cli, "get_settings", lambda: Settings(_env_file=None))
    monkeypatch.setattr("drugpaper.services.Services.from_settings", lambda settings: Service())
    monkeypatch.setattr("drugpaper.orchestration.text_pipeline.TextPipeline", Pipeline)
    result = runner.invoke(cli.app, ["text-facts", "--doc", "doc_test", "--force"])
    assert result.exit_code == 1 and json.loads(result.output)["status"] == "failed"
    assert holder == {"args": ("doc_test", True), "closed": True}
