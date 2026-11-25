"""An isolated scheme can identify compound sets without inventing their structures."""

import copy
import json

import pytest
from jsonschema import Draft202012Validator
from pydantic import ValidationError, create_model

from drugpaper.facts.compound_instances import CompoundInstance
from drugpaper.facts.compound_series import numeric_members
from drugpaper.facts.figure_models import VisualExtraction
from drugpaper.facts.molecular_delivery import deliver
from drugpaper.skills.execution import PackageOutput
from drugpaper.skills.figure import scheme_relations
from drugpaper.skills.loader import load_package
from drugpaper.skills.protocol import SkillBudget, SkillContext, ToolCaller
from drugpaper.skills.registry import BUILTIN_ROOT

ROOT = BUILTIN_ROOT / "scheme_extract"


def example():
    return json.loads((ROOT / "tests/cases/3.response.json").read_text())


def context():
    return SkillContext(
        "one",
        "doc",
        1,
        "doc/r1/fig",
        "run",
        "trace",
        None,
        ToolCaller(frozenset()),
        None,
        SkillBudget(0, 30),
    )


def test_label_ranges_are_not_substituent_products():
    assert numeric_members("1–10") == [str(i) for i in range(1, 11)]
    labels = numeric_members("16–27,45–47")
    assert len(labels) == 15 and "26" in labels and "28" not in labels
    assert numeric_members("10a-c") is None  # No unsupported suffix arithmetic.
    assert numeric_members("10-1") is None
    assert numeric_members("1-999999999") is None


def test_public_wire_retains_unresolved_series_without_other_tables():
    skill = load_package(ROOT)
    assert skill.ok, skill.errors
    payload = example()
    Draft202012Validator(skill.output_schema).validate(payload)
    wire = create_model("IsolatedSchemeWire", __base__=PackageOutput)
    wire.package_schema, wire.semantic_model = skill.output_schema, VisualExtraction
    assert wire.model_validate(payload).model_dump() == payload
    entity = VisualExtraction.model_validate(payload).pathways[0].steps[0].reactants[0]
    assert entity.mol_id is None and entity.scaffold_mol_id == 0
    assert entity.compound_series.member_count == 10 and not entity.variants


@pytest.mark.parametrize(
    "mistake",
    [
        "range_instance",
        "range_as_molecule",
        "options_as_assignment",
        "wrong_count",
        "invented_member",
        "false_ok",
    ],
)
def test_series_cannot_silently_become_concrete(mistake):
    payload = example()
    family = payload["pathways"][0]["steps"][0]["reactants"][0]
    if mistake == "range_instance":
        with pytest.raises(ValidationError, match="range is a series"):
            CompoundInstance(
                reference="1–10",
                scaffold_mol_id=0,
                substituents={"X": "O, S"},
                evidence_text="Visible options",
            )
        return
    if mistake == "range_as_molecule":
        family.pop("compound_series")
        family["mol_id"] = 0
    elif mistake == "options_as_assignment":
        family["substituents"] = {"X": "O, S"}
    elif mistake == "wrong_count":
        family["compound_series"]["member_count"] = 6
    elif mistake == "invented_member":
        family["compound_series"]["members"][-1] = "99"
    elif mistake == "false_ok":
        payload["status"] = "ok"
    with pytest.raises(ValidationError):
        VisualExtraction.model_validate(payload)


def test_backfill_preserves_scaffold_without_materializing_ten_copies():
    raw = example()
    original = copy.deepcopy(raw)
    boxes = [{"mol_id": i, "node_ref": f"doc/r1/fig.m{i}", "segment": 0} for i in range(2)]
    structures = [
        {
            "node_ref": b["node_ref"],
            "structure_id": f"s{i}",
            "e_smiles_raw": "CCO",
            "validation": {"valid": True},
        }
        for i, b in enumerate(boxes)
    ]
    result = deliver(raw, boxes, structures)
    assert not result["errors"]
    assert raw == original
    family = result["payload"]["pathways"][0]["steps"][0]["reactants"][0]
    assert family["compound_series"]["structure_status"] == "unresolved"
    assert family["scaffold_structure"]["formula"] is None
    assert family["variants"] == [] and "molecular_structure" not in family
    assert "assembled_molecule" not in family
    assert result["coverage"]["resolved_count"] == 2  # Box binding is independent of member resolution.
    assert result["payload"]["status"] == "unclear"


async def test_arrow_semantics_do_not_turn_a_series_into_ten_reactions():
    payload = example()
    step = payload["pathways"][0]["steps"][0]
    unresolved = await scheme_relations({"extraction": payload}, context())
    assert not unresolved.fact_proposals
    assert unresolved.artifacts[0].payload["errors"][0]["code"] == "unresolved_arrow_semantics"
    step["relationship_type"] = "reaction"
    reaction = await scheme_relations({"extraction": payload}, context())
    assert not reaction.fact_proposals
    assert reaction.artifacts[0].payload["errors"][0]["code"] == "unresolved_series_reaction"
    for kind, relation in [("optimization", "optimizes_to"), ("selection", "selects_member")]:
        step["relationship_type"] = kind
        result = await scheme_relations({"extraction": payload}, context())
        edges = result.artifacts[0].payload["relations"]
        assert len(edges) == 1
        assert (edges[0]["source"], edges[0]["target"], edges[0]["type"]) == ("1–10", "3", relation)
        assert edges[0]["condition"] is None
    step["evidence_text"] = ""
    assert not (await scheme_relations({"extraction": payload}, context())).fact_proposals


async def test_runtime_does_not_persist_a_series_as_one_compound(figure_env):
    from sqlalchemy import select

    from drugpaper.persistence.models import LabelClaimRow

    sv, _, _, run = figure_env
    detected = await run("molecule_detect")
    for box in detected.artifacts[0].payload["boxes"]:
        await run("molecule_recognize", ref=f"doc_chem/r1/{box['local_id']}")
    family = example()["pathways"][0]["steps"][0]["reactants"][0]
    # Root molecules also exercises the document-label lookup path: no lookup should occur.
    result = await run(
        "mol_backfill",
        inputs={"contract": "universal", "extraction_payload": {"status": "unclear", "molecules": [family]}},
    )
    delivered = result.artifacts[0].payload
    assert not delivered["errors"]
    assert delivered["payload"]["molecules"][0]["scaffold_structure"]["formula"] is None
    async with sv.db.session() as session:
        claims = list((await session.execute(select(LabelClaimRow))).scalars())
    assert not claims


def test_markush_expands_substituents_inside_reactant_and_survives_binding():
    payload = example()
    endpoint = payload["pathways"][0]["steps"][0]["reactants"][0]
    interpretation = endpoint["markush"]
    assert set(interpretation["variable_options"]) == {"X", "Y", "Z"}
    assert {c["group_text"] for c in interpretation["substitution_cases"]} == {
        "–OCH3",
        "–OCH2F",
        "–OCHF2",
        "–SCH3",
        "–SCH2F",
        "–SCHF2",
    }
    assert sum(len(c["assignments"]) for c in interpretation["substitution_cases"]) == 8
    assert endpoint["compound_series"]["member_count"] == 10 and endpoint["variants"] == []
    assert VisualExtraction.model_validate(payload).pathways[0].steps[0].reactants[0].markush is not None
    boxes = [{"mol_id": i, "node_ref": f"doc/r1/fig.m{i}"} for i in range(2)]
    structures = [
        {
            "node_ref": b["node_ref"],
            "structure_id": f"s{i}",
            "e_smiles_raw": "CCO",
            "validation": {"valid": True},
        }
        for i, b in enumerate(boxes)
    ]
    result = deliver(payload, boxes, structures)
    assert not result["errors"]
    assert result["payload"]["pathways"][0]["steps"][0]["reactants"][0]["markush"] == interpretation
    assert "assembled_molecule" not in str(result["payload"])


@pytest.mark.parametrize(
    "mistake",
    [
        "undeclared_option",
        "invented_variable",
        "case_as_compound",
        "missing_explanation",
        "duplicate_assignment",
    ],
)
def test_markush_cases_remain_local_substituent_possibilities(mistake):
    payload = example()
    endpoint = payload["pathways"][0]["steps"][0]["reactants"][0]
    case = endpoint["markush"]["substitution_cases"][0]
    if mistake == "undeclared_option":
        case["assignments"][0]["X"] = "N"
    elif mistake == "invented_variable":
        case["assignments"][0]["R"] = "Me"
    elif mistake == "case_as_compound":
        case["reference"] = "1"
    elif mistake == "missing_explanation":
        endpoint.pop("markush")
    else:
        case["assignments"].append(copy.deepcopy(case["assignments"][0]))
    with pytest.raises(ValidationError):
        VisualExtraction.model_validate(payload)


def test_generic_product_expands_each_position_without_assigning_numbered_compounds():
    payload = example()
    endpoint = payload["pathways"][0]["steps"][0]["products"][0]
    endpoint.pop("mol_id")
    endpoint.update(reference="16–27,45–47", scaffold_mol_id=1, variants=[])
    options = {"R": ["COMe", "COOMe", "COOH", "CONH2", "SO2NH2", "SOAlk"], "X": ["H", "Cl"], "Y": ["CH", "N"]}
    endpoint["compound_series"] = {
        "members": numeric_members(endpoint["reference"]),
        "member_count": 15,
        "variable_definitions": {k: ", ".join(v) for k, v in options.items()},
        "structure_status": "unresolved",
        "unresolved_reason": "No per-compound assignments in this image",
        "evidence_text": "Range printed beside generic scaffold",
    }
    endpoint["markush"] = {
        "description": "R is a substituent, X is a ring substituent, Y is a ring-atom choice, independently located as drawn",
        "variable_options": options,
        "substitution_cases": [
            {
                "expression": symbol,
                "group_text": value,
                "assignments": [{symbol: value}],
                "basis": "printed",
                "evidence_text": "Option printed in the legend",
            }
            for symbol, values in options.items()
            for value in values
        ],
        "expansion_status": "partial",
        "notes": "Alk is not defined here; list it literally without inventing chain lengths. Number-to-structure assignments remain unknown.",
        "evidence_text": "R/X/Y definitions printed under this product",
    }
    skill = load_package(ROOT)
    Draft202012Validator(skill.output_schema).validate(payload)
    parsed = VisualExtraction.model_validate(payload).pathways[0].steps[0].products[0]
    assert parsed.compound_series.member_count == 15
    assert len(parsed.markush.substitution_cases) == 10
    assert parsed.markush.variable_options["X"] == ["H", "Cl"]
    assert (
        parsed.markush.variable_options["X"]
        != payload["pathways"][0]["steps"][0]["reactants"][0]["markush"]["variable_options"]["X"]
    )
    assert parsed.variants == []


def test_missing_markush_evidence_reports_exact_fields_for_repair():
    skill = load_package(ROOT)
    payload = example()
    endpoints = []
    for pathway in payload["pathways"]:
        for step in pathway["steps"]:
            for side in ("reactants", "products"):
                for index, endpoint in enumerate(step.get(side, [])):
                    if endpoint.get("markush"):
                        endpoint["markush"].pop("evidence_text", None)
                        endpoints.append((side, index))
    assert endpoints
    wire = create_model("MissingMarkushEvidence", __base__=PackageOutput)
    wire.package_schema = skill.output_schema
    with pytest.raises(ValidationError) as caught:
        wire.model_validate(payload)
    message = str(caught.value)
    assert "/markush: 'evidence_text' is a required property" in message
    assert "is not valid under any of the given schemas" not in message
    assert "Generic scaffold with" not in message


async def test_missing_series_error_is_not_masked_as_link_quarantine_failure():
    from drugpaper.gateway.base import SchemaViolation
    from drugpaper.skills.execution import execute

    payload = example()
    payload["pathways"][0]["steps"][0]["reactants"][0].pop("compound_series")
    failure = SchemaViolation(
        json.dumps(payload),
        ["pathways.0.steps.0.reactants.0: numeric compound ranges require compound_series"],
    )

    class FailedModel:
        async def chat(self, request, *, task):
            raise failure

    ctx = context()
    ctx.model = FailedModel()
    with pytest.raises(SchemaViolation) as caught:
        await execute(load_package(ROOT), {}, ctx)
    assert caught.value is failure
    assert "compound_series" in str(caught.value)


def test_unexpanded_reactant_keeps_generic_form_through_binding():
    payload = example()
    endpoint = payload["pathways"][0]["steps"][0]["reactants"][0]
    markush = endpoint["markush"]
    markush.update(
        substitution_cases=[],
        expansion_status="not_expanded",
        notes="反应物仅保留通式，不展开取代组合。",
    )
    schema = load_package(ROOT).output_schema
    Draft202012Validator(schema).validate(payload)
    VisualExtraction.model_validate(payload)
    boxes = [{"mol_id": i, "node_ref": f"doc/r1/fig.m{i}"} for i in range(2)]
    result = deliver(payload, boxes, [])
    bound = result["payload"]["pathways"][0]["steps"][0]["reactants"][0]
    assert bound["markush"] == markush
    assert bound["compound_series"]["member_count"] == 10
    assert bound["scaffold_mol_id"] == 0


def test_not_expanded_markush_cannot_claim_expansion_cases():
    payload = example()
    endpoint = payload["pathways"][0]["steps"][0]["reactants"][0]
    endpoint["markush"]["expansion_status"] = "not_expanded"
    with pytest.raises(ValidationError, match="not_expanded Markush must not contain"):
        VisualExtraction.model_validate(payload)


def test_context_assignment_uses_current_scaffold_without_external_complete_structure():
    payload = example()
    endpoint = copy.deepcopy(payload["pathways"][0]["steps"][0]["reactants"][0])
    endpoint["compound_series"]["structure_status"] = "partial"
    endpoint["variants"] = [
        {
            "reference": "3",
            "scaffold_mol_id": 0,
            "substituents": {"XCHYZ": "OCH2F"},
            "evidence_text": "Current figure scaffold 0; reference table row 3 supplies the group assignment only.",
        }
    ]
    payload["pathways"][0]["steps"][0]["products"] = [endpoint]
    VisualExtraction.model_validate(payload)
    boxes, structures = [], []
    for i, raw in enumerate(["*c1ccccc1<sep><a>0:XCHYZ</a>", "CCO"]):
        ref = f"doc/r1/current.m{i}"
        boxes.append({"mol_id": i, "node_ref": ref})
        structures.append(
            {"node_ref": ref, "structure_id": f"s{i}", "e_smiles_raw": raw, "validation": {"valid": True}}
        )
    out = deliver(payload, boxes, structures)
    member = out["payload"]["pathways"][0]["steps"][0]["products"][0]["variants"][0]
    assert member["assembly_status"] == "completed", out["errors"]
    assert member["assembled_molecule"]["canonical_smiles"]
    assert all(b["node_ref"].startswith("doc/r1/current.") for b in member["assembly_provenance"]["bindings"])


def test_partial_product_cannot_claim_mapping_without_any_member():
    payload = example()
    product = copy.deepcopy(payload["pathways"][0]["steps"][0]["reactants"][0])
    product["compound_series"]["structure_status"] = "partial"
    product["variants"] = []
    payload["pathways"][0]["steps"][0]["products"] = [product]
    with pytest.raises(ValidationError, match="partial product series requires"):
        VisualExtraction.model_validate(payload)


async def test_current_figure_scope_never_calls_document_structure_lookup(figure_env):
    _, _, _, run = figure_env
    result = await run(
        "mol_backfill",
        inputs={
            "contract": "universal",
            "structure_source_scope": "current_figure",
            "extraction_payload": {"status": "unclear", "molecules": [{"reference": "26"}]},
        },
    )
    data = result.artifacts[0].payload
    assert data["structure_source_scope"] == "current_figure"
    assert any(e["code"] == "current_figure_structure_missing" for e in data["errors"])
    entity = data["payload"]["molecules"][0]
    assert "reference_binding" not in entity and "molecular_structure" not in entity
