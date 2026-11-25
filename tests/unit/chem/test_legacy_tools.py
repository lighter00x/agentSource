from __future__ import annotations

import copy

import pytest

from drugpaper.chem.backfill import backfill
from drugpaper.chem.splicer.adapter import infer_role, substitute
from drugpaper.gateway.base import ToolCall
from drugpaper.tools.builtin.chem import make_chem_tools
from drugpaper.tools.protocol import ToolContext
from drugpaper.tools.registry import ToolRegistry


@pytest.mark.parametrize(
    "caption,definitions,expected",
    [
        ("C1CCCCC1*<sep><a>6:R</a>", {"R": "Me"}, ["CC1CCCCC1"]),
        ("*CC*<sep><a>0:R</a><a>3:R'</a>", {"R": "Cl", "R'": "Br"}, ["ClCCBr"]),
        ("*CC*<sep><a>0:R</a><a>3:R′</a>", {"R": "Cl", "R′": "Br"}, ["ClCCBr"]),
        ("C1CCCCC1*<sep><a>6:R</a>", {"R": "[H]"}, ["C1CCCCC1"]),
        ("c1ccccc1<sep><r>0:R[1]</r>", {"R1": "Cl"}, ["Clc1ccccc1"]),
        ("c1ccccc1<sep><r>0:R?2</r>", {"R": "Cl"}, ["Clc1ccc(Cl)cc1", "Clc1cccc(Cl)c1", "Clc1ccccc1Cl"]),
        ("*CC*<sep><a>0:R1</a><a>3:R2</a>", {"R1": "Cl", "R2": "Br"}, ["ClCCBr"]),
        ("C1CCCCC1*<sep><a>6:R</a>", {"R": "OC<sep>"}, ["COC1CCCCC1"]),
        ("*C*<sep><a>1:CH2?3</a>", {}, ["*CCCC"]),
    ],
)
def test_migrated_splicing_regressions(caption, definitions, expected):
    result = substitute(caption, definitions)
    assert result.status == "completed" and result.products == expected, result


def test_prime_does_not_fall_back_to_unprimed():
    result = substitute("*CC*<sep><a>0:R</a><a>3:R'</a>", {"R": "Cl"})
    assert result.error_code == "E_UNDEFINED_VARIABLE"


@pytest.mark.parametrize(
    "caption,defs,limit,code",
    [
        ("*CC*<sep><a>0:R1</a><a>3:R2</a>", {"R1": ["Cl", "Br"], "R2": ["Cl", "Br"]}, 2, "E_OUTPUT_LIMIT"),
        ("c1ccccc1<sep><r>0:R</r>", {"R": ["Cl", "Br"]}, 1, "E_OUTPUT_LIMIT"),
        ("C1CCCCC1*<sep><a>6:R</a>", {}, 64, "E_UNDEFINED_VARIABLE"),
        ("C1CCCCC1*<sep><a>6:R</a>", {"R position": 4}, 64, "positional_variable"),
    ],
)
def test_limits_and_error_contract(caption, defs, limit, code):
    assert substitute(caption, defs, max_outputs=limit).error_code == code


def test_backfill_is_non_mutating_and_supports_plural_ids():
    data = {"molecules": [{"mol_id": 0}, {"mol_ids": [0, 1]}, {"mol_id": True}, {"mol_id": 9}]}
    before = copy.deepcopy(data)
    output, errors = backfill(data, {0: "CC", 1: "OC"})
    assert data == before and output["molecules"][1]["esmiles_list"] == ["CC", "OC"]
    assert len(errors) == 2 and errors[0]["code"] == "E_UNKNOWN_MOL_ID"


async def test_chem_tools_injected_through_registry():
    registry = ToolRegistry(make_chem_tools())
    ctx = ToolContext(run_id="run", trace_id="tr")
    result = await registry.invoke(
        ctx,
        ToolCall(
            id="call",
            name="markush.substitute",
            arguments={"caption": "C1CCCCC1*<sep><a>6:R</a>", "definitions": {"R": "Me"}},
        ),
    )
    assert result.ok and result.data["products"] == ["CC1CCCCC1"]
    assert infer_role("C1CCCCC1*<sep><a>6:R</a>") == "scaffold"
    assert infer_role("CC") == "unknown"


def test_attachment_end_and_precompat_substructure_are_preserved():
    assert substitute("C1CCCCC1*<sep><a>6:R</a>", {"R": "OCC*"}).products == ["OCCC1CCCCC1"]
    result = substitute("CC<sep><s>**<sep><a>0:L[3]</a><a>1:R[3]</a>|Sg:n|</s>", {"L3": "Cl", "R3": "Br"})
    assert result.products == ["CC<sep><s>ClBr<sep>|Sg:n|</s>"]


@pytest.mark.parametrize(
    "name,args",
    [
        ("esmiles.infer_role", {"esmiles": "CC*"}),
        ("esmiles.parse", {"esmiles": "CC*<sep><a>2:R</a>"}),
        ("mol.backfill", {"data": {"molecules": [{"mol_id": 0}]}, "molecules": {0: "CCO"}}),
        ("mol.deliver", {"data": {"molecules": []}, "boxes": [], "structures": []}),
    ],
)
async def test_each_chem_tool_returns_serializable_contract(name, args):
    import json

    original = copy.deepcopy(args)
    result = await ToolRegistry(make_chem_tools()).invoke(
        ToolContext(run_id="chem", trace_id="chem"), ToolCall(id="chem", name=name, arguments=args)
    )
    assert result.ok and not result.truncated and args == original
    assert json.loads(result.to_message_content())["ok"] is True


async def test_chem_tool_failure_is_explicit_and_does_not_invent_products():
    result = await ToolRegistry(make_chem_tools()).invoke(
        ToolContext(run_id="chem", trace_id="chem"),
        ToolCall(
            id="bad",
            name="markush.substitute",
            arguments={"caption": "*CC*<sep><a>0:R</a><a>3:R'</a>", "definitions": {"R": "Cl"}},
        ),
    )
    assert (
        not result.ok and result.data["error_code"] == "E_UNDEFINED_VARIABLE" and not result.data["products"]
    )
