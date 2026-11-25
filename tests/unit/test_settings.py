from __future__ import annotations

from pathlib import Path

import pytest

from drugpaper.config.models_file import (
    MissingEnvVarError,
    ModelsConfigError,
    load_default_model,
    load_models_file,
    resolve_task,
)
from drugpaper.config.settings import Settings, get_settings

REPO_MODELS_YAML = Path(__file__).resolve().parents[2] / "config" / "models.yaml"
ENV = {"LLM_BASE_URL": "http://llm.local:8000/v1", "LLM_API_KEY": "sk-test", "LLM_MODEL": "qwen-test"}


def test_settings_defaults_and_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)  # 避免读到仓库里的 .env
    monkeypatch.setenv("DRUGPAPER_ENV", "test")
    monkeypatch.setenv("RUNS_DIR", str(tmp_path / "r"))
    s = Settings()
    assert s.env == "test"
    assert s.runs_dir == tmp_path / "r"
    assert s.models_config_path == Path("config/models.yaml")


def test_empty_env_values_fall_back_to_defaults(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DATABASE_URL", "")
    monkeypatch.setenv("BLOB_ROOT", "  ")
    s = Settings()
    assert s.database_url.startswith("sqlite+aiosqlite:///")
    assert s.blob_root == Path("./data/blobs")


def test_get_settings_cached() -> None:
    get_settings.cache_clear()
    assert get_settings() is get_settings()


def test_repo_models_yaml_resolves() -> None:
    file = load_models_file(REPO_MODELS_YAML)
    rm = resolve_task(file, "default", ENV)
    assert rm.base_url == ENV["LLM_BASE_URL"]
    assert rm.api_key == "sk-test"
    assert rm.model_name == "qwen-test"
    assert rm.model_key == "text_llm"
    assert rm.api == "responses"
    assert rm.thinking_toggle == "none"
    assert rm.extra_body["reasoning"]["effort"] == "medium"
    assert rm.timeout_s == 60


def test_load_default_model_uses_settings_path(tmp_path: Path) -> None:
    s = Settings(models_config_path=REPO_MODELS_YAML, runs_dir=tmp_path)
    rm = load_default_model(s, env=ENV)
    assert rm.model_name == "qwen-test"


@pytest.mark.parametrize("missing", ["LLM_BASE_URL", "LLM_API_KEY", "LLM_MODEL"])
def test_missing_env_var_is_reported(missing: str) -> None:
    env = {k: v for k, v in ENV.items() if k != missing}
    file = load_models_file(REPO_MODELS_YAML)
    with pytest.raises(MissingEnvVarError) as ei:
        resolve_task(file, "default", env)
    assert ei.value.var == missing
    assert missing in str(ei.value)


def test_invalid_yaml(tmp_path: Path) -> None:
    p = tmp_path / "models.yaml"
    p.write_text("providers: [unclosed\n", encoding="utf-8")
    with pytest.raises(ModelsConfigError, match="YAML"):
        load_models_file(p)


def test_schema_error(tmp_path: Path) -> None:
    p = tmp_path / "models.yaml"
    p.write_text("providers: {}\nmodels: {}\n", encoding="utf-8")  # 缺 stages / tasks
    with pytest.raises(ModelsConfigError, match="字段校验失败"):
        load_models_file(p)


def test_unknown_task_and_broken_refs(tmp_path: Path) -> None:
    file = load_models_file(REPO_MODELS_YAML)
    with pytest.raises(ModelsConfigError, match="未在 models.yaml"):
        resolve_task(file, "nope", ENV)
    file.pools["llm"] = []
    with pytest.raises(ModelsConfigError, match="池"):
        resolve_task(file, "default", ENV)


def test_missing_file(tmp_path: Path) -> None:
    with pytest.raises(ModelsConfigError, match="找不到"):
        load_models_file(tmp_path / "none.yaml")
