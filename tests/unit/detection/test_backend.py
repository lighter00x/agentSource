from pathlib import Path

import pytest

from drugpaper.detection.backend import YoloBackend
from drugpaper.detection.models import DetectionError


def test_missing_model_explicit_failure(tmp_path: Path):
    for path in (None, tmp_path / "missing.pt"):
        with pytest.raises(DetectionError) as error:
            YoloBackend(path, "cpu").signature()
        assert error.value.code == "E_MODEL_NOT_READY"


def test_weight_path_is_configurable_in_dotenv_and_environment(tmp_path, monkeypatch):
    from drugpaper.config.settings import Settings

    monkeypatch.delenv("YOLO_MODEL_PATH", raising=False)
    first = tmp_path / "model first.pt"
    second = tmp_path / "model second.pt"
    config = tmp_path / "yolo.env"
    config.write_text(f'YOLO_MODEL_PATH="{first}"\nYOLO_DEVICE=cpu\nYOLO_CONF=0.2\n')
    configured = Settings(_env_file=config)
    assert configured.yolo_model_path == first
    assert configured.yolo_device == "cpu" and configured.yolo_conf == 0.2
    monkeypatch.setenv("YOLO_MODEL_PATH", str(second))
    assert Settings(_env_file=config).yolo_model_path == second
