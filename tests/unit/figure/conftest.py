import pytest

from drugpaper.persistence.models import NodeRow
from tests.unit.chem.test_pipeline_skills import molecule_env

__all__ = ["molecule_env"]


@pytest.fixture
async def figure_env(molecule_env):
    sv, backend, bundle, run = molecule_env
    sv.settings.figure_exploration_enabled = False
    async with sv.db.session() as session:
        node = await session.get(NodeRow, ("doc_chem", 1, "p000.g000"))
        node.geometry = {**node.geometry, "regions": node.geometry["regions"][:1]}
    yield sv, backend, bundle, run
