"""Reusable molecular workflows must survive actual L1/L3 disclosure and schema loading."""

from __future__ import annotations

import json
import re

import pytest
from jsonschema import Draft202012Validator

from drugpaper.capabilities.index import Capability, CapabilityIndex
from drugpaper.capabilities.meta_tools import make_meta_tools
from drugpaper.capabilities.tokens import estimate_tokens
from drugpaper.capabilities.toolset import DisclosureToolset
from drugpaper.facts.figure_models import VisualExtraction
from drugpaper.skills.loader import load_package
from drugpaper.skills.registry import BUILTIN_ROOT
from drugpaper.tools.protocol import ToolContext
from drugpaper.tools.registry import ToolRegistry
from tests.fakes.fake_llm import call

VISUAL = (
    "scheme_extract",
    "table_extract",
    "structures_extract",
    "molecular_docking_extract",
    "protac_architecture_extract",
    "metabolic_pathway_extract",
    "pharmacophore_model_extract",
    "chart_digitize",
    "common_extract",
)
CORE = (
    "molecule_detect",
    "molecule_recognize",
    "som_render",
    "mol_backfill",
    "sar_record",
    "measurement_map",
    "scheme_relations",
    "markush_assemble",
    "panel_split",
    "figure_self_check",
)


@pytest.mark.parametrize("name", VISUAL + CORE)
async def test_workflow_is_discoverable_and_readable_through_meta_tools(name):
    skill = load_package(BUILTIN_ROOT / name)
    assert skill.ok, skill.errors
    registry = ToolRegistry([])
    index = CapabilityIndex(None, registry)
    index.entries[name] = Capability(name, "skill", skill.l0, skill.l1, skill=skill)
    toolset = DisclosureToolset(registry, [], index)
    for tool in make_meta_tools(index, toolset):
        registry.register(tool)
    ctx = ToolContext(run_id="workflow", trace_id="workflow")
    card = await registry.invoke(ctx, call("describe_capability", {"name": name}))
    assert card.ok and card.data
    # An appendix in SKILL.md would not be exposed by the real L1 loader.
    assert "molecular-workflow.md" in card.data["l1"]
    assert card.data["tokens"]["L1"] <= 350
    for section in ("本类步骤", "字段与实例", "执行与交付", "失败与复核", "验收清单"):
        result = await registry.invoke(
            ctx,
            call(
                "read_skill_resource",
                {
                    "name": name,
                    "section": f"molecular-workflow.md#{section}",
                },
            ),
        )
        assert result.ok and result.data and not result.truncated
        expected = skill.read_resource(f"molecular-workflow.md#{section}")
        assert result.data["text"] == expected and not expected.endswith("…")
        assert estimate_tokens(expected) <= 1500
    # L3 instructions do not grant an unloaded skill invocation or load every tool.
    assert not ctx.capability_state.get("loaded")


@pytest.mark.parametrize("name", VISUAL)
def test_public_output_examples_match_current_schema_and_semantics(name):
    skill = load_package(BUILTIN_ROOT / name)
    text = skill.read_resource("molecular-workflow.md#合法示例", max_tokens=4000)
    blocks = re.findall(r"```json\n(.*?)\n```", text, re.S)
    assert blocks
    for block in blocks:
        example = json.loads(block)
        Draft202012Validator(skill.output_schema).validate(example)
        if name not in {"table_extract", "chart_digitize"}:
            VisualExtraction.model_validate(example)
        # Chart postprocessing has no compound_instances: examples must not rely
        # on permissive JSON Schema extras that Pydantic would silently discard.
        if name == "chart_digitize":
            assert "compound_instances" not in example


@pytest.mark.parametrize("name", tuple(n for n in VISUAL if n not in {"table_extract", "chart_digitize"}))
def test_visual_prompt_assembles_id_only_workflow_and_correct_image_order(name):
    skill = load_package(BUILTIN_ROOT / name)
    system = skill.system_text()
    assert "common/mol_instances" in skill.fragments
    assert "来源互斥" in system and "后续mol_backfill" in system
    assert "先提供带编号框" in system and "编号可能不连续" in system
    assert 'disposition="unclear"' in system
    assert "先给整图" not in system and "不得出现在输出的任何位置" not in system
    rendered = skill.render("main", target={"ref": "image-ref", "caption": "Fig 1"}, inputs={})
    assert rendered and "SMILES" in rendered
