from __future__ import annotations

from pathlib import Path

import pytest

FIXTURE_DIR = Path(__file__).resolve().parents[2] / "fixtures" / "jmedchem_2c00949"
STEM = "10_1021_acs_jmedchem_2c00949"
LAYOUT_PATH = FIXTURE_DIR / f"{STEM}_layout.json"
MIDDLE_PATH = FIXTURE_DIR / f"{STEM}_middle.json"
PDF_PATH = FIXTURE_DIR / f"{STEM}_origin.pdf"
SNAPSHOT_PATH = FIXTURE_DIR / "ir_snapshot.json"

needs_real = pytest.mark.skipif(not LAYOUT_PATH.exists(), reason="样例论文 fixtures 缺失")
