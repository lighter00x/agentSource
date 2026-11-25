from __future__ import annotations

from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"
MINI_LAYOUT = FIXTURES / "mini_layout" / "sample_layout.json"
REAL_FIXTURE_DIR = FIXTURES / "jmedchem_2c00949"


def real_layout_path() -> Path | None:
    """真实样例论文的 layout.json；未复制进仓库时返回 None。"""
    if not REAL_FIXTURE_DIR.exists():
        return None
    found = sorted(REAL_FIXTURE_DIR.glob("*_layout.json")) or sorted(REAL_FIXTURE_DIR.glob("layout.json"))
    return found[0] if found else None


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption("--smoke", action="store_true", default=False, help="运行访问真实模型端点的冒烟测试")


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    if config.getoption("--smoke"):
        return
    skip = pytest.mark.skip(reason="冒烟测试默认跳过，使用 --smoke 运行")
    for item in items:
        if "smoke" in item.keywords:
            item.add_marker(skip)


@pytest.fixture
def mini_layout_path() -> Path:
    return MINI_LAYOUT
