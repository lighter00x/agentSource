"""Unseen chemistry and executable skills, not edited paper artifacts."""

import copy

import pytest

from drugpaper.chem.normalize import canonical
from drugpaper.facts.molecular_delivery import deliver


def scene(scaffold="*c1ccccc1<sep><a>0:R</a>", group="*C"):
    boxes = [{"mol_id": i, "node_ref": f"doc/r1/figure.m{i}", "segment": 0} for i in range(2)]
    structures = [
        {
            "structure_id": f"s{i}",
            "node_ref": b["node_ref"],
            "e_smiles_raw": raw,
            "validation": {"valid": True},
        }
        for b, i, raw in zip(boxes, range(2), [scaffold, group], strict=True)
    ]
    return boxes, structures


def variant(label="10a", group=1):
    return {
        "reference": label,
        "scaffold_mol_id": 0,
        "substituents": {"R": group},
        "evidence_text": f"Printed {label}: R = group {group}",
    }


def test_scheme_family_instances_are_backfilled_in_place_with_provenance():
    boxes, structures = scene()
    original = {
        "optimization_steps": [
            {
                "reference": "10a-c",
                "condition": "reagent, 80 °C",
                "variants": [variant(), variant("10b", "Cl"), variant("10c", "H")],
            }
        ]
    }
    snapshot = copy.deepcopy(original)
    out = deliver(original, boxes, structures)
    assert not out["errors"]
    products = out["payload"]["optimization_steps"][0]["variants"]
    assert [v["assembled_molecule"]["canonical_smiles"] for v in products] == [
        canonical(s).canonical_smiles for s in ["Cc1ccccc1", "Clc1ccccc1", "c1ccccc1"]
    ]
    assert [v["assembled_molecule"]["formula"] for v in products] == ["C7H8", "C6H5Cl", "C6H6"]
    assert products[0]["assembly_provenance"]["bindings"][1]["structure_id"] == "s1"
    assert original == snapshot and out["original"] == snapshot
    assert out["molecule_objects"][0]["role"] == "scaffold"
    assert out["molecule_objects"][1]["role"] == "substituent"


@pytest.mark.parametrize(
    "template,group,expected",
    [
        ("*CC<sep><a>0:R</a>", "*N", "CCN"),
        ("*C(=O)O<sep><a>0:R</a>", "*CC", "CCC(=O)O"),
        ("*c1ccncc1<sep><a>0:R</a>", "*O", "Oc1ccncc1"),
        ("*C1CCCCC1<sep><a>0:R</a>", "*C#N", "N#CC1CCCCC1"),
    ],
)
def test_generic_attachment_works_on_unseen_scaffold_families(template, group, expected):
    boxes, structures = scene(template, group)
    out = deliver({"compound_instances": [variant()]}, boxes, structures)
    assert not out["errors"], out["errors"]
    product = out["payload"]["compound_instances"][0]["assembled_molecule"]
    assert product["canonical_smiles"] == canonical(expected).canonical_smiles


@pytest.mark.parametrize(
    "change,reason",
    [
        ({"substituents": {"R": 99}}, "unresolved_group"),
        ({"substituents": {"R": "CCO"}}, "unsupported_literal"),
        ({"substituents": {"Q": "Me"}}, "absent_from_template"),
        ({"substituents": {"R": True}}, "validation"),
        ({"mol_id": 1}, "exactly one"),
        ({"evidence_text": ""}, "validation"),
        ({"assembled_molecule": {"canonical_smiles": "CCO"}}, "validation"),
    ],
)
def test_invalid_instances_never_get_full_products(change, reason):
    boxes, structures = scene()
    item = {**variant(), **change}
    out = deliver({"compound_instances": [item], "molecules": [{"mol_id": 1}]}, boxes, structures)
    assert out["errors"] and out["coverage"]["status"] == "needs_review"
    failed = out["payload"]["compound_instances"][0]
    assert failed["assembly_status"] == "needs_review" and "assembled_molecule" not in failed


def test_missing_assignment_does_not_produce_partial_molecule():
    boxes, structures = scene("*C(*)O<sep><a>0:R</a><a>2:R[2]</a>")
    out = deliver({"compound_instances": [variant()], "molecules": [{"mol_id": 1}]}, boxes, structures)
    assert out["errors"]
    assert "assembled_molecule" not in out["payload"]["compound_instances"][0]


def test_prime_variables_remain_independent():
    boxes, structures = scene("*C(*)O<sep><a>0:R</a><a>2:R′</a>")
    item = variant(group="Me")
    item["substituents"]["R′"] = "Cl"
    out = deliver({"compound_instances": [item], "molecules": [{"mol_id": 1}]}, boxes, structures)
    assert not out["errors"], out["errors"]
    assert (
        out["payload"]["compound_instances"][0]["assembled_molecule"]["canonical_smiles"]
        == canonical("CC(O)Cl").canonical_smiles
    )


def test_row_evidence_cannot_be_attached_to_a_different_compound():
    boxes, structures = scene()
    item = {**variant(), "row_index": 1}
    out = deliver(
        {"rows": [[{"text": "10a"}], [{"text": "10b"}]], "compound_instances": [item]}, boxes, structures
    )
    assert any("instance_label_not_in_source_row" in e.get("reason", "") for e in out["errors"])


def test_full_drawings_have_separate_instance_without_assembly():
    boxes, structures = scene("CCO")
    item = {"reference": "17", "mol_id": 0, "evidence_text": "17 below complete drawing"}
    out = deliver({"compound_instances": [item], "molecules": [{"mol_id": 1}]}, boxes, structures)
    assert not out["errors"]
    full = out["payload"]["compound_instances"][0]
    assert full["assembled_molecule"]["formula"] == "C2H6O"
    assert full["assembly_provenance"]["method"] == "direct_recognition"


async def test_skill_runtime_persists_full_instances_and_real_route_edges(molecule_env):
    from sqlalchemy import select

    from drugpaper.persistence.models import FigureRelationRow, LabelClaimRow, StructureCandidateRow

    sv, _, _, run = molecule_env
    detected = await run("molecule_detect")
    nodes = detected.artifacts[0].payload["boxes"]
    import httpx2

    from drugpaper.gateway.molparser import MolParserClient

    raw_structures = iter(["*c1ccccc1<sep><a>0:R</a>", "CCO"])

    def recognize(request):
        if request.url.path == "/health":
            return httpx2.Response(200, json={"queue_size": 0, "queue_maxsize": 10})
        return httpx2.Response(200, json={"results": [{"input_index": 0, "esmi": next(raw_structures)}]})

    await sv._molparser.aclose()
    sv._molparser = MolParserClient(
        "http://mock", http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(recognize))
    )
    for node in nodes:
        await run("molecule_recognize", ref=f"doc_chem/r1/{node['local_id']}")
    payload = {
        "base_info": {"reference": "9", "mol_id": 1},
        "optimization_steps": [
            {
                "reference": "10a-c",
                "condition": "a",
                "variants": [variant("10a", "Me"), variant("10b", "Cl"), variant("10c", "H")],
            }
        ],
    }
    result = await run("mol_backfill", inputs={"contract": "universal", "extraction_payload": payload})
    filled = result.artifacts[0].payload
    assert not filled["errors"]
    instances = filled["payload"]["optimization_steps"][0]["variants"]
    assert all(i["assembled_molecule"]["structure_id"] for i in instances)
    await run("scheme_relations", inputs={"extraction": filled["payload"]})
    async with sv.db.session() as session:
        claims = list(
            (await session.execute(select(LabelClaimRow).where(LabelClaimRow.is_active.is_(True)))).scalars()
        )
        edges = list((await session.execute(select(FigureRelationRow))).scalars())
        candidates = list((await session.execute(select(StructureCandidateRow))).scalars())
    assert {c.payload["label"] for c in claims} == {"9", "10a", "10b", "10c"}
    assert len(candidates) == 5
    assert {(e.payload["source"], e.payload["target"]) for e in edges} == {("9", f"10{x}") for x in "abc"}
    assert all(e.payload["product_structure_id"] for e in edges)


async def test_figure_agent_sar_uses_id_only_skill_and_attaches_row_properties(figure_env):
    import json

    import httpx2
    from langgraph.checkpoint.memory import InMemorySaver

    from drugpaper.agents.figure.graph import FigureAgent
    from drugpaper.gateway.base import ChatResponse
    from drugpaper.gateway.molparser import MolParserClient
    from drugpaper.gateway.task_router import TaskRouter
    from tests.unit.figure.test_agent import CLASSIFICATION, FigureFake

    sv, _, bundle, _ = figure_env
    await sv._molparser.aclose()

    def recognize(request):
        if request.url.path == "/health":
            return httpx2.Response(200, json={"queue_size": 0, "queue_maxsize": 10})
        return httpx2.Response(
            200, json={"results": [{"input_index": 0, "esmi": "*c1ccccc1<sep><a>0:R</a>"}]}
        )

    sv._molparser = MolParserClient(
        "http://mock", http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(recognize))
    )

    class Model(FigureFake):
        async def chat(self, req, *, task="default"):
            if task == "visual_classify":
                return ChatResponse(
                    content=json.dumps(
                        {
                            **CLASSIFICATION,
                            "image_type": "sar_table",
                            "has_chemical_structures": True,
                            "has_markush_scaffold": True,
                        }
                    )
                )
            if task == "figure_structure":
                self.calls.append((task, req))
                text = "\n".join(m.text() for m in req.messages)
                assert "*c1ccccc1" not in text and "e_smiles_raw" not in text
                return ChatResponse(
                    content=json.dumps(
                        {
                            "headers": ["Compound", "R", "IC50 (nM)"],
                            "rows": [["10a", "Me", "50"], ["10b", "Cl", "70"]],
                            "footnote_markers": {},
                            "cell_details": [],
                            "orientation": "compounds_as_rows",
                            "scaffold_mol_id": 0,
                            "compound_instances": [
                                {**variant("10a", "Me"), "row_index": 0},
                                {**variant("10b", "Cl"), "row_index": 1},
                            ],
                        }
                    )
                )
            return await super().chat(req, task=task)

    model = Model()
    model.router = TaskRouter.from_file(sv.settings.models_config_path)
    bundle.models = model
    report = await FigureAgent(sv, InMemorySaver()).run("doc_chem/r1/p000.g000")
    final = report.backfilled_extraction
    assert final["sar_records"]["status"] == "completed", report.open_questions
    assert [i["assembled_molecule"]["formula"] for i in final["compound_instances"]] == ["C7H8", "C6H5Cl"]
    assert [[p["value_raw"] for p in i["properties"]] for i in final["compound_instances"]] == [
        ["50"],
        ["70"],
    ]
    assert all(i["assembled_molecule"]["structure_id"] for i in final["compound_instances"])
    assert all("assembled_molecule" not in i for i in report.pre_backfill_extraction["compound_instances"])
    assert "CCO" not in json.dumps(report.pre_backfill_extraction)


async def test_parallel_route_families_require_visible_pairing():
    from drugpaper.skills.figure import scheme_relations
    from drugpaper.skills.protocol import SkillBudget, SkillContext, ToolCaller

    ctx = SkillContext(
        user_id="one",
        document_id="doc",
        revision=1,
        target_ref="doc/r1/fig",
        run_id="test",
        trace_id="test",
        model=None,
        tools=ToolCaller(frozenset()),
        blobs=None,
        budget=SkillBudget(0, 30),
    )
    sources = {
        "reference": "9a-b",
        "variants": [{"reference": f"9{x}", "assembly_status": "completed"} for x in "ab"],
    }
    targets = {
        "reference": "10a-b",
        "variants": [
            {
                "reference": f"10{x}",
                "assembly_status": "completed",
                "assembled_molecule": {"structure_id": f"s{x}"},
            }
            for x in "ab"
        ],
    }
    payload = {"pathways": [{"steps": [{"reactants": [sources], "products": [targets]}]}]}
    rejected = await scheme_relations({"extraction": payload}, ctx)
    assert rejected.artifacts[0].payload["errors"] and not rejected.fact_proposals
    payload["pathways"][0]["steps"][0]["variant_links"] = [
        {"source": f"9{x}", "target": f"10{x}", "evidence_text": "parallel arrows"} for x in "ab"
    ]
    result = await scheme_relations({"extraction": payload}, ctx)
    edges = result.artifacts[0].payload["relations"]
    assert {(e["source"], e["target"]) for e in edges} == {("9a", "10a"), ("9b", "10b")}
    assert not result.artifacts[0].payload["errors"]


def test_same_compound_on_distinct_sar_rows_preserves_each_row():
    boxes, structures = scene()
    specs = [{**variant("10a", "Me"), "row_index": i} for i in range(2)]
    out = deliver(
        {
            "rows": [[{"text": "10a"}, {"text": "50"}], [{"text": "10a"}, {"text": "70"}]],
            "compound_instances": specs,
            "molecules": [{"mol_id": 1}],
        },
        boxes,
        structures,
    )
    assert not out["errors"], out["errors"]
    assert [i["row_index"] for i in out["payload"]["compound_instances"]] == [0, 1]


def test_instance_json_schema_rejects_label_only_and_mixed_sources():
    from jsonschema import Draft202012Validator

    from drugpaper.facts.compound_instances import CompoundInstance

    schema = Draft202012Validator(CompoundInstance.model_json_schema())
    assert list(schema.iter_errors({"reference": "10a", "evidence_text": "label only"}))
    assert list(schema.iter_errors({**variant(), "mol_id": 1}))
    schema.validate(variant())


@pytest.mark.parametrize(
    "template,group,reason",
    [
        ("*c1ccccc1<sep><a>3:R</a>", "*C", "template_annotation_not_on_placeholder"),
        ("*c1ccccc1<sep><a>0:R</a>", "CC", "group_has_no_explicit_attachment"),
    ],
)
def test_attachment_sites_are_not_guessed_or_repaired(template, group, reason):
    boxes, structures = scene(template, group)
    out = deliver({"compound_instances": [variant()]}, boxes, structures)
    assert any(reason in e.get("reason", "") for e in out["errors"])
    assert "assembled_molecule" not in out["payload"]["compound_instances"][0]


def test_explicit_stereo_instances_do_not_share_unspecified_structures():
    from rdkit import Chem

    boxes, structures = scene("CC(O)F")
    specs = [
        {"reference": f"({cip})-10", "mol_id": 0, "evidence_text": f"Printed ({cip})-10 label"}
        for cip in "RS"
    ]
    out = deliver({"compound_instances": specs, "molecules": [{"mol_id": 1}]}, boxes, structures)
    assert not out["errors"], out["errors"]
    full = out["payload"]["compound_instances"]
    assert (
        full[0]["assembled_molecule"]["canonical_smiles"] != full[1]["assembled_molecule"]["canonical_smiles"]
    )
    for instance, cip in zip(full, "RS", strict=True):
        mol = Chem.MolFromSmiles(instance["assembled_molecule"]["canonical_smiles"])
        assert (
            Chem.FindMolChiralCenters(mol, includeUnassigned=True, useLegacyImplementation=False)[0][1] == cip
        )
        assert instance["assembly_provenance"]["stereochemistry_from_explicit_label"]


async def test_separate_endpoint_aliases_on_one_box_are_also_a_series():
    from drugpaper.skills.figure import scheme_relations
    from drugpaper.skills.protocol import SkillBudget, SkillContext, ToolCaller

    ctx = SkillContext(
        "one", "doc", 1, "doc/r1/fig", "run", "trace", None, ToolCaller(frozenset()), None, SkillBudget(0, 30)
    )
    payload = {
        "pathways": [
            {
                "steps": [
                    {
                        "reactants": [{"reference": f"({cip})-23", "mol_id": 0} for cip in "RS"],
                        "products": [{"reference": f"({cip})-26", "mol_id": 1} for cip in "RS"],
                    }
                ]
            }
        ]
    }
    result = await scheme_relations({"extraction": payload}, ctx)
    assert not result.fact_proposals
    assert result.artifacts[0].payload["errors"][0]["code"] == "ambiguous_series_links"


def test_gateway_semantic_validation_rejects_links_borrowed_from_another_step():
    from pydantic import ValidationError, create_model

    from drugpaper.facts.figure_models import VisualExtraction
    from drugpaper.skills.execution import PackageOutput

    wire = create_model("SchemeSemanticOutput", __base__=PackageOutput)
    wire.package_schema = VisualExtraction.model_json_schema()
    wire.semantic_model = VisualExtraction
    payload = {
        "pathways": [
            {
                "steps": [
                    {
                        "reactants": [{"reference": "56", "mol_id": 0}],
                        "products": [{"reference": "23", "mol_id": 1}],
                        "variant_links": [{"source": "23", "target": "26", "evidence_text": "wrong arrow"}],
                    }
                ]
            }
        ]
    }
    with pytest.raises(ValidationError, match="this arrow"):
        wire.model_validate(payload)
    payload["pathways"][0]["steps"][0]["variant_links"] = []
    wire.model_validate(payload)


def test_quarantined_links_preserve_molecule_inputs_and_failure_evidence():
    from drugpaper.facts.figure_models import VisualExtraction
    from drugpaper.facts.reaction_semantics import quarantine_links

    raw = {
        "compound_instances": [{"reference": "10", "mol_id": 0, "evidence_text": "visible 10"}],
        "pathways": [
            {
                "steps": [
                    {
                        "reactants": [{"reference": "9", "mol_id": 1}],
                        "products": [{"reference": "10", "mol_id": 0}],
                        "variant_links": [{"source": "10", "target": "11", "evidence_text": "wrong step"}],
                    }
                ]
            }
        ],
    }
    saved = copy.deepcopy(raw)
    recovered = quarantine_links(raw)
    assert recovered["status"] == "unclear" and recovered["needs_review"]
    assert recovered["compound_instances"] == raw["compound_instances"]
    step = recovered["pathways"][0]["steps"][0]
    assert (
        not step["variant_links"]
        and step["rejected_variant_links"] == raw["pathways"][0]["steps"][0]["variant_links"]
    )
    assert recovered["semantic_errors"][0]["field_path"] == "$.pathways[0].steps[0]"
    VisualExtraction.model_validate(recovered)
    assert raw == saved


async def test_agent_recovers_valid_molecules_after_semantic_model_failure(figure_env):
    import json

    from langgraph.checkpoint.memory import InMemorySaver

    from drugpaper.agents.figure.graph import FigureAgent
    from drugpaper.gateway.base import ChatResponse, SchemaViolation
    from drugpaper.gateway.task_router import TaskRouter
    from tests.unit.figure.test_agent import CLASSIFICATION, FigureFake

    sv, _, bundle, _ = figure_env
    raw = {
        "compound_instances": [{"reference": "9", "mol_id": 0, "evidence_text": "drawn 9"}],
        "pathways": [
            {
                "steps": [
                    {
                        "reactants": [{"reference": "8", "mol_id": 0}],
                        "products": [{"reference": "9", "mol_id": 0}],
                        "variant_links": [
                            {"source": "9", "target": "10", "evidence_text": "cross-arrow link"}
                        ],
                    }
                ]
            }
        ],
    }

    class Model(FigureFake):
        async def chat(self, req, *, task="default"):
            if task == "visual_classify":
                return ChatResponse(
                    content=json.dumps(
                        {
                            **CLASSIFICATION,
                            "image_type": "synthetic_scheme_single",
                            "has_chemical_structures": True,
                        }
                    )
                )
            if task == "figure_structure":
                raise SchemaViolation(json.dumps(raw), ["variant_link endpoints must belong to this arrow"])
            if task == "figure_structure_review":
                # Salvaged ID-only molecules still require the independent review
                # introduced by the pre-backfill gate.
                return ChatResponse(
                    content=json.dumps(
                        {
                            "coverage": [
                                {
                                    "dimension": dimension,
                                    "verdict": "pass",
                                    "evidence": "Verified retained current-figure IDs",
                                }
                                for dimension in [
                                    "topology",
                                    "molecule_placeholders",
                                    "markush_completeness",
                                    "source_scope",
                                ]
                            ],
                            "issues": [],
                        }
                    )
                )
            return await super().chat(req, task=task)

    model = Model()
    model.router = TaskRouter.from_file(sv.settings.models_config_path)
    bundle.models = model
    report = await FigureAgent(sv, InMemorySaver()).run("doc_chem/r1/p000.g000")
    assert report.status == "needs_review"
    assert report.backfilled_extraction["compound_instances"][0]["assembled_molecule"]["formula"] == "C2H6O"
    assert report.backfilled_extraction["route_errors"][0]["code"] == "invalid_variant_link"
    assert (
        report.pre_backfill_extraction["pathways"][0]["steps"][0]["rejected_variant_links"]
        == raw["pathways"][0]["steps"][0]["variant_links"]
    )
