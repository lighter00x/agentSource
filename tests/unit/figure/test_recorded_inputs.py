"""Recorded inputs must match the effective crop/numbered images, not parent material."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from drugpaper.agents.figure.graph import FigureAgent
from drugpaper.gateway.base import ImagePart
from drugpaper.skills.materials import Materials
from drugpaper.workbench.runner import DebugAgent


def state():
    return {
        "node_ref": "doc/r1/fig",
        "document_id": "doc",
        "user_id": "default",
        "run_id": "run",
        "trace_id": "tr",
        "trace": [],
    }


@pytest.mark.parametrize("molecule", [True, False])
async def test_skill_record_uses_child_crop_or_explicit_numbered_images(monkeypatch, molecule):
    original = ImagePart(blob_ref="original")
    crop = ImagePart(blob_ref="crop")
    numbered = ImagePart(blob_ref="numbered")
    parent = SimpleNamespace(materials=Materials(target={"type": "figure"}, images=[original]))
    child = SimpleNamespace(
        materials=Materials(
            target={
                "type": "molecule",
                "attributes": {"reading_index": 7},
                "geometry": {"local_bbox": {"x0": 0.1, "y0": 0.2, "x1": 0.4, "y1": 0.5}},
            },
            images=[crop],
        )
    )
    bundle = SimpleNamespace(
        registry=SimpleNamespace(
            resolve=AsyncMock(return_value=SimpleNamespace(version="1", content_hash="hash"))
        ),
        context_for=AsyncMock(return_value=child),
    )
    recorder = SimpleNamespace(event=AsyncMock())
    agent = DebugAgent.__new__(DebugAgent)
    agent._ctx = AsyncMock(return_value=(bundle, parent))
    agent.recorder = recorder
    s = state()

    async def run_skill(self, s, name, *args, **kwargs):
        s["trace"].append({"step": name})
        return {"done": True}

    monkeypatch.setattr(FigureAgent, "_run_skill", run_skill)
    name = "molecule_recognize" if molecule else "scheme_extract"
    kwargs = (
        {"ref": "doc/r1/fig.m007"} if molecule else {"images": [numbered], "reference_images": [original]}
    )
    await agent._run_skill(s, name, **kwargs)
    kind, data = recorder.event.call_args_list[0].args
    assert kind == "skill_start"
    assert data["images"][0]["blob_ref"] == ("crop" if molecule else "numbered")
    assert data["images"][0]["role"] == ("molecule_crop" if molecule else "numbered_target")
    if molecule:
        assert data["molecule"]["mol_id"] == 7
        assert data["target"] == "doc/r1/fig.m007"
    else:
        assert data["reference_images"][0]["blob_ref"] == "original"
        assert data["reference_images"][0]["role"] == "original_reference"


@pytest.mark.parametrize("missing", [True, False])
async def test_figure_extraction_requires_valid_numbered_material(missing):
    original = ImagePart(blob_ref="original")
    numbered = ImagePart(blob_ref="numbered")
    ctx = SimpleNamespace(materials=Materials(target={}, images=[original]), extras={})

    async def prepare(context):
        if missing:
            raise ValueError("编号图缺失或过期")
        context.materials.images = [numbered]
        context.extras["reference_images"] = [original]

    bundle = SimpleNamespace(
        registry=SimpleNamespace(
            list_visible=AsyncMock(
                return_value=[
                    SimpleNamespace(name="scheme_extract", manifest=SimpleNamespace(vision_structuring=True))
                ]
            )
        ),
        prepare_vision=prepare,
    )
    agent = SimpleNamespace(_ctx=AsyncMock(return_value=(bundle, ctx)), _run_skill=AsyncMock(return_value={}))
    s = {
        **state(),
        "chain": ["som_render", "scheme_extract"],
        "material": {"text_tokens": [], "caption": "", "source_text": []},
        "classification": {},
    }
    if missing:
        with pytest.raises(ValueError, match="编号图"):
            await FigureAgent._structure_once(agent, s)
        agent._run_skill.assert_not_awaited()
    else:
        await FigureAgent._structure_once(agent, s)
        kwargs = agent._run_skill.call_args.kwargs
        assert kwargs["images"] == [numbered]
        assert kwargs["reference_images"] == [original]
