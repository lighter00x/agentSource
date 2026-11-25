"""Business export keeps chemistry and topology while isolating debug data."""

import copy
import json

from drugpaper.facts.flowchart_delivery import compact_result, summarize_result


def source():
    variant = {
        "reference": "1",
        "substituents": {"X": "O", "XCHYZ": "OCH2F"},
        "attachments": ["XCHYZ"],
        "position_definitions": {"CHO": "4"},
        "assembly_status": "completed",
        "assembled_molecule": {"valid": True, "canonical_smiles": "COC", "formula": "C2H6O"},
        "bbox": [1, 2, 3, 4],
        "assembly_provenance": {"debug": True},
    }
    return {
        "image_type": "synthetic_scheme_single",
        "schema_version": "figure-result/1.0",
        "status": "needs_review",
        "quality": {"review": "debug"},
        "exploration": {"trace": []},
        "extraction": {
            "pathways": [
                {
                    "steps": [
                        {
                            "relationship_type": "optimization",
                            "reactants": [
                                {
                                    "reference": "A",
                                    "variants": [variant],
                                    "markush": {"variable_options": {"R": ["Me", "Et"]}},
                                }
                            ],
                            "products": [
                                {
                                    "reference": "1–10",
                                    "variants": [variant],
                                    "scaffold_esmiles": "*CO<sep><a>0:R</a>",
                                    "markush": {
                                        "variable_options": {"R": ["Me", "Et"]},
                                        "evidence_text": "debug",
                                    },
                                }
                            ],
                            "conditions": {
                                "reagents": ["NaH"],
                                "solvent": "THF",
                                "time": "12h",
                                "temperature": "0 °C",
                                "bbox": [1, 2],
                            },
                        }
                    ]
                }
            ]
        },
    }


def test_chemistry_only_and_no_mutation():
    original = source()
    before = copy.deepcopy(original)
    result = compact_result(original)
    step = result["pathways"][0]["steps"][0]
    assert step["conditions"] == {"reagents": ["NaH"], "solvent": "THF", "time": "12h", "temperature": "0 °C"}
    assert "variants" not in step["reactants"][0]
    member = step["products"][0]["variants"][0]
    assert member["smiles"] == "COC" and member["attachments"] == ["XCHYZ"]
    assert (
        member["substituents"]
        == before["extraction"]["pathways"][0]["steps"][0]["products"][0]["variants"][0]["substituents"]
    )
    for forbidden in (
        "bbox",
        "candidates",
        "assembly_provenance",
        "evidence_text",
        "exploration",
        "quality",
    ):
        assert forbidden not in json.dumps(result)
    assert result["status"] == "needs_review"
    assert compact_result(result) == result
    member["attachments"].clear()
    assert original == before


def test_legacy_steps_conditions_and_unknown_assembly():
    result = compact_result(
        {
            "extraction": {
                "pathways": [
                    {
                        "base_info": {"reference": "A"},
                        "optimization_steps": [
                            {
                                "reference": "B",
                                "condition": "rt, 2h",
                                "assembly_status": "failed",
                                "assembled_molecule": {"valid": False, "canonical_smiles": "bad"},
                            },
                            {"reference": "C", "condition": None},
                        ],
                    }
                ]
            }
        }
    )
    first, second = result["pathways"][0]["steps"]
    assert first["reactants"] == [{"reference": "A"}]
    assert first["conditions"] == "rt, 2h"
    assert "smiles" not in first["products"][0]
    assert second["reactants"][0]["reference"] == "B"
    assert second["conditions"] is None


def test_non_flowchart_contract_unchanged():
    original = {"image_type": "table", "extraction": {"rows": [{"value": 1}], "pathways": []}}
    assert compact_result(original) == original


def test_nested_conditions_and_member_correspondence():
    original = source()
    step = original["extraction"]["pathways"][0]["steps"][0]
    step["conditions"] = {"temperature": {"value": 25, "unit": "°C"}}
    step["variant_links"] = [{"source": "A", "target": "1", "evidence_text": "debug"}]
    output = compact_result(original)["pathways"][0]["steps"][0]
    assert output["conditions"] == step["conditions"]
    assert output["variant_links"] == [{"source": "A", "target": "1"}]


async def test_saved_delivery_and_api_projection(figure_env):
    from drugpaper.api.figure_results import result_response
    from drugpaper.facts.figure_result import persist_result

    sv, _, _, _ = figure_env
    original = source()
    payload = {
        "document_id": "doc_chem",
        "node_ref": "doc_chem/r1/p000.g000",
        "run_id": "wd_test",
        "image_type": original["image_type"],
        "backfilled_extraction": original["extraction"],
    }
    metadata, full = await persist_result(sv.store_for("default"), payload)
    saved = json.loads(await sv.store_for("default").get(metadata["delivery_blob_ref"]))
    assert saved == compact_result(full)
    assert json.loads(result_response(full, True, "delivery").body) == saved
    assert json.loads(result_response(full, True).body) == summarize_result(full)
    assert json.loads(await sv.store_for("default").get(metadata["summary_blob_ref"])) == summarize_result(
        full
    )
    assert json.loads(result_response(full, True, "full").body) == full
    assert json.loads(await sv.store_for("default").get(metadata["blob_ref"])) == full


def test_summary_keeps_all_product_expansions_without_debug_fields():
    from drugpaper.facts.flowchart_delivery import summarize_result

    original = source()
    before = copy.deepcopy(original)
    step = original["extraction"]["pathways"][0]["steps"][0]
    step["products"][0]["assembly_candidates"] = [{"reference": f"candidate-{i}"} for i in range(100)]
    summary = summarize_result(original)
    route = summary["pathways"][0]["steps"][0]
    assert route["relationship_type"] == "optimization"
    assert route["conditions"]["solvent"] == "THF"
    product = route["products"][0]
    assert product["reference"] == "1–10"
    assert product["markush"]["variable_options"] == {"R": ["Me", "Et"]}
    assert product["markush"]["scaffold"] == "*CO<sep><a>0:R</a>"
    assert len(product["variants"]) == 1
    assert product["variants"][0]["smiles"] == "COC"
    assert "candidates" not in product
    assert product["variants"][0]["position_definitions"] == {"CHO": "4"}
    assert "molecules" not in summary
    reactant = route["reactants"][0]
    assert "variants" not in reactant and "candidates" not in reactant
    text = json.dumps(summary)
    for forbidden in [
        "attachments",
        "bbox",
        "candidates",
        "evidence_text",
        "quality",
    ]:
        assert forbidden not in text
    assert summary["status"] == "needs_review"
    original["extraction"]["pathways"][0]["steps"][0]["products"][0].pop("assembly_candidates")
    assert original == before
    assert summarize_result(summary) == summary


def test_summary_keeps_inline_endpoints_and_reaction_conditions():
    from drugpaper.facts.flowchart_delivery import summarize_result

    original = source()
    steps = original["extraction"]["pathways"][0]["steps"]
    steps.append(
        {
            "relationship_type": "selection",
            "reactants": copy.deepcopy(steps[0]["products"]),
            "products": [{"reference": "lead", "molecular_structure": {"canonical_smiles": "COC"}}],
            "conditions": None,
        }
    )
    summary = summarize_result(original)
    first, second = summary["pathways"][0]["steps"]
    assert first["products"][0]["reference"] == second["reactants"][0]["reference"]
    assert "variants" in first["products"][0] and "variants" not in second["reactants"][0]
    assert second["products"][0]["smiles"] == "COC"
    assert second["conditions"] is None
    assert summarize_result({"image_type": "table", "extraction": {"rows": []}}) == {
        "image_type": "table",
        "extraction": {"rows": []},
    }


async def test_report_followed_by_deterministic_deliver_stage(figure_env):
    from drugpaper.agents.figure.graph import FigureAgent
    from drugpaper.workbench.store import LABELS, STAGES

    sv, _, _, _ = figure_env
    original = source()
    report = {
        "document_id": "doc_chem",
        "node_ref": "doc_chem/r1/p000.g000",
        "run_id": "old-run",
        "image_type": original["image_type"],
        "status": "needs_review",
        "backfilled_extraction": original["extraction"],
    }
    state = {"report": copy.deepcopy(report)}
    agent = FigureAgent(sv)
    await agent._deliver(state)
    assert state["report"] == report
    assert state["summary_result"]["schema_version"] == "flowchart-summary/1.1"
    assert state["summary_result"]["status"] == "needs_review"
    assert STAGES[-2:] == ["report", "deliver"] and len(STAGES) == len(LABELS)
    assert ("report", "deliver") in agent.graph.builder.edges


def test_summary_keeps_actual_products_and_excludes_theoretical_candidates():
    original = source()
    product = original["extraction"]["pathways"][0]["steps"][0]["products"][0]
    failed = copy.deepcopy(product["variants"][0])
    failed.update(
        reference="2",
        assembly_status="failed",
        assembled_molecule={"valid": False, "canonical_smiles": "invalid"},
    )
    product["variants"].append(failed)
    product["assembly_candidates"] = [
        {
            "substituents": {"R": "Et"},
            "assembled_molecule": {"valid": True, "canonical_smiles": "CCO"},
            "status": "completed",
        },
        {"substituents": {"R": "Alk"}, "status": "completed"},
    ]
    summary = summarize_result(original)
    node = summary["pathways"][0]["steps"][0]["products"][0]
    assert [v["reference"] for v in node["variants"]] == ["1", "2"]
    assert node["variants"][0]["smiles"] == "COC"
    assert node["variants"][1]["smiles"] is None and node["variants"][1]["status"] == "failed"
    assert "candidates" not in node
    assert len(product["assembly_candidates"]) == 2
    # Referencing this series as a subsequent reactant must not erase its products.
    original["extraction"]["pathways"][0]["steps"].append(
        {"reactants": [copy.deepcopy(product)], "products": [{"reference": "lead"}]}
    )
    again = summarize_result(original)
    assert again["pathways"][0]["steps"][0]["products"][0]["variants"] == node["variants"]


async def test_backfill_preview_never_generates_final_projection_before_report(figure_env):
    from drugpaper.api.figure_results import result_response
    from drugpaper.facts.figure_result import persist_result

    sv, _, _, _ = figure_env
    original = source()
    payload = {
        "document_id": "doc_chem",
        "node_ref": "doc_chem/r1/p000.g000",
        "run_id": "wd_preview",
        "image_type": original["image_type"],
        "backfilled_extraction": original["extraction"],
    }
    metadata, preview = await persist_result(sv.store_for("default"), payload, "backfilled")
    assert preview["status"] == "pending_review" and preview["processing_stage"] == "backfilled"
    assert "summary_blob_ref" not in metadata and "delivery_blob_ref" not in metadata
    assert summarize_result(preview) == preview
    for view in ["summary", "delivery", "full"]:
        assert json.loads(result_response(preview, False, view).body) == preview
    final_metadata, final = await persist_result(sv.store_for("default"), payload, "reported")
    assert final["processing_stage"] == "reported"
    assert final_metadata["summary_blob_ref"] and final_metadata["delivery_blob_ref"]
    assert json.loads(result_response(final, False).body)["schema_version"] == "flowchart-summary/1.1"
