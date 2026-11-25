"""Classification runs before detection and must not inherit extraction-only rules."""

from __future__ import annotations

import json

import pytest

from drugpaper.gateway.base import ImagePart
from drugpaper.skills.execution import execute
from drugpaper.skills.loader import load_package
from drugpaper.skills.materials import Materials
from drugpaper.skills.protocol import SkillBudget, SkillContext, ToolCaller
from drugpaper.skills.registry import BUILTIN_ROOT
from tests.fakes.fake_llm import FakeLLM, text


@pytest.mark.parametrize("name", ["visual_classify", "visual_reclassify"])
async def test_outbound_classification_request_has_no_molecule_reference_rules(name):
    skill = load_package(BUILTIN_ROOT / name)
    assert skill.ok, skill.errors
    payload = {
        "image_type": "data_table",
        "legacy_type_id": 11,
        "has_chemical_structures": False,
        "has_markush_scaffold": False,
        "chart_types": [],
        "panel_count": 1,
        "evidence": ["The image is a numeric table without chemical structures."],
        "confidence": 0.95,
        "needs_review": False,
    }
    model = FakeLLM([text(json.dumps(payload))])
    ctx = SkillContext(
        user_id="default",
        document_id="doc_test",
        revision=1,
        target_ref="doc_test/r1/p000.g000",
        run_id="classification-test",
        trace_id="classification-test",
        model=model,
        tools=ToolCaller(frozenset()),
        blobs=None,
        budget=SkillBudget(2, 30),
        materials=Materials(target={"caption": "Table 1", "type": "table"}),
    )
    assert ctx.materials is not None
    ctx.materials.images = [ImagePart(blob_ref="original-figure")]
    # Existing downstream material must not turn classification into extraction.
    ctx.materials.target["children"] = [{"ref": "detected-molecule", "attributes": {"reading_index": 42}}]
    ctx.extras["reference_images"] = [ImagePart(blob_ref="numbered-figure")]

    result = await execute(skill, {}, ctx)

    assert result.artifacts[0].payload == payload
    assert model.tasks == [name]
    assert len(model.requests) == 1
    request = model.requests[0]
    prompt = "\n".join(message.text() for message in request.messages)
    assert "根据附图和图注分类" in prompt
    assert "## 数据边界" in prompt
    assert "## 不编造" in prompt
    assert "分子标识规则" not in prompt
    assert "候选框真实性校验" not in prompt
    assert "mol_id" not in prompt
    assert "reading_index" not in prompt
    assert "detected-molecule" not in prompt
    assert not request.tools
    images = [
        part
        for message in request.messages
        if isinstance(message.content, list)
        for part in message.content
        if isinstance(part, ImagePart)
    ]
    assert [image.blob_ref for image in images] == ["original-figure"]
    assert request.response_schema.model_json_schema() == skill.output_schema
