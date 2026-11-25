"""Cross-visual member evidence without hard-coded figure names, numbers, or query order."""

import copy

import pytest

from drugpaper.agents.figure.reference_assignments import (
    apply_reference_assignments,
    blocked_instance_paths,
    enforce_reference_assignments,
    find_reference_visuals,
    record_reference_assignments,
    supplement_errors,
)
from drugpaper.docsrc.ir_source import IrDocumentSource
from drugpaper.domain.geometry import BBox, Geometry, Region
from drugpaper.domain.nodes import DocumentNode, IRDocument, PageInfo
from drugpaper.facts.molecular_delivery import deliver

TARGET = "d/r1/p000.g000"
RELATED = "d/r1/p007.g000"
PATH = "/pathways/0/steps/0/products/0"


def source():
    nodes = []
    for i, kind, label, text in [
        (0, "figure", "Figure 8", "Optimization overview"),
        (7, "scheme", "Scheme 12", "Preparation of compounds 73–74"),
        (9, "table", "Table 5", "Activity of compounds 85–89"),
    ]:
        nodes.append(
            DocumentNode(
                document_id="d",
                revision=1,
                local_id=f"p{i:03}.g000",
                node_type=kind,
                label=label,
                text_raw=text,
                text_norm=text,
                geometry=Geometry(regions=[Region(page=i, bbox=BBox(x0=0, y0=0, x1=1, y1=1))]),
            )
        )
    return IrDocumentSource(
        IRDocument(
            document_id="d",
            revision=1,
            pages=[PageInfo(page=i, width_pt=600, height_pt=800) for i in range(10)],
            nodes=nodes,
        )
    )


def test_visual_number_and_merged_footnote_are_not_compound_matches():
    src = source()
    node = src.node("d/r1/p009.g000")
    node.label = "Table 73"
    node.text_norm = "Table 73. Unrelated receptor response\na Values for compounds 73–74 appear elsewhere."
    result = find_reference_visuals(src, TARGET, {"references": ["73–74"], "terms": ["R"]})
    assert [c["node_id"] for c in result["candidates"]] == [RELATED]
    assert result["candidates"][0]["matched_terms"] == []


def test_ocr_split_ranges_and_joined_conjunction_are_navigation_only():
    src = source()
    src.node("d/r1/p009.g000").text_norm = "Table 5. Compounds8 5-89 and90-92"
    result = find_reference_visuals(src, TARGET, {"references": ["85–92"]})
    table = next(c for c in result["candidates"] if c["node_id"] == "d/r1/p009.g000")
    assert table["reference_coverage"] == 1
    assert set(table["matched_references"]) == {str(i) for i in range(85, 93)}


def extraction():
    return {
        "status": "unclear",
        "pathways": [
            {
                "steps": [
                    {
                        "products": [
                            {
                                "reference": "73–74",
                                "scaffold_mol_id": 0,
                                "variants": [],
                                "compound_series": {
                                    "members": ["73", "74"],
                                    "member_count": 2,
                                    "structure_status": "unresolved",
                                    "unresolved_reason": "Missing assignments",
                                    "evidence_text": "Printed range",
                                },
                            }
                        ]
                    }
                ]
            }
        ],
    }


def state():
    return {
        "node_ref": TARGET,
        "extraction": extraction(),
        "exploration": {
            "applied_evidence_ids": ["e2"],
            "evidence": [
                {
                    "id": "e1",
                    "tool": "inspect_visual",
                    "result": {"node_ref": TARGET, "image_refs": [{"page": 0}]},
                },
                {
                    "id": "e2",
                    "tool": "inspect_visual",
                    "result": {"node_ref": RELATED, "image_refs": [{"page": 7}]},
                },
                {"id": "preview", "tool": "preview_pages", "result": {"image_refs": [{"page": 7}]}},
                {"id": "text", "tool": "grep", "result": {"matches": []}},
            ],
        },
    }


def args(**change):
    return {
        "product_path": PATH,
        "evidence_ids": ["e2"],
        "relevance": "The related visual explicitly identifies the same numbered members and R assignments.",
        "assignments": [
            {"reference": "73", "substituents": {"R": "Cl"}, "evidence_text": "Source row 73 states R=Cl"}
        ],
        **change,
    }


def register(s=None, **change):
    s = s or state()
    return s, record_reference_assignments(source(), s, args(**change))


def test_deleted_pathway_retains_reference_error_without_crashing_delivery():
    s, _ = register()
    empty = {"status": "unclear", "pathways": []}
    delivery = {"payload": copy.deepcopy(empty), "errors": [], "coverage": {"status": "completed"}}
    enforce_reference_assignments(delivery, empty, s["exploration"])
    assert delivery["coverage"]["status"] == "needs_review"
    assert any(e["code"] == "reference_target_changed" for e in delivery["errors"])
    assert delivery["payload"]["pathways"] == []


def test_discovery_uses_caption_and_member_overlap_without_specific_scheme_or_series():
    out = find_reference_visuals(source(), TARGET, {"references": ["73", "74"], "terms": ["Preparation"]})
    assert out["candidates"][0]["node_id"] == RELATED
    assert out["candidates"][0]["matched_references"] == ["73", "74"]
    assert TARGET not in [r["node_id"] for r in out["candidates"]]
    assert "navigation" in out["note"]


@pytest.mark.parametrize("evidence", ["preview", "text", "invented", "e1"])
def test_supplement_requires_actually_viewed_related_visual(evidence):
    with pytest.raises(ValueError):
        register(evidence_ids=[evidence])


def test_supplement_is_separate_and_restores_deleted_confirmed_fields():
    s, out = register(
        assignments=[
            {
                "reference": "73",
                "substituents": {"R": "Cl"},
                "position_definitions": {
                    "CHO position": "4",
                    "XCHYZ position": "2'",
                    "X": "O",
                    "Y, Z": "H, F",
                },
                "evidence_text": "Printed exact positions",
            }
        ]
    )
    original = copy.deepcopy(s["extraction"])
    assert s["extraction"] == original
    result = apply_reference_assignments(original, s["exploration"])
    member = result["pathways"][0]["steps"][0]["products"][0]["variants"][0]
    assert member["reference"] == "73" and member["substituents"] == {"R": "Cl"}
    assert member["position_definitions"]["CHO position"] == "4"
    assert "ra1" in member["evidence_text"]
    assert not supplement_errors(result, s["exploration"]["reference_assignments"])
    assert s["extraction"] == original
    assert out["record"]["sources"][0]["node_ref"] == RELATED


def test_reference_conflicts_are_not_silently_overwritten():
    s, _ = register()
    ex = extraction()
    ex["pathways"][0]["steps"][0]["products"][0]["variants"] = [
        {
            "reference": "73",
            "scaffold_mol_id": 0,
            "substituents": {"R": "F"},
            "evidence_text": "New model conflicts",
        }
    ]
    fixed = apply_reference_assignments(ex, s["exploration"])
    assert fixed["pathways"][0]["steps"][0]["products"][0]["variants"][0]["substituents"]["R"] == "F"
    assert s["exploration"]["reference_assignment_errors"]
    assert blocked_instance_paths(fixed, s["exploration"]) == [
        "$.pathways[0].steps[0].products[0].variants[0]"
    ]


def test_missing_source_constraints_block_assembly_before_persistence():
    s, _ = register()
    ex = extraction()
    ex["pathways"][0]["steps"][0]["products"][0]["variants"] = [
        {
            "reference": "73",
            "scaffold_mol_id": 0,
            "substituents": {"R": "F"},
            "evidence_text": "Conflicting model assignment",
        }
    ]
    blocked = blocked_instance_paths(ex, s["exploration"])
    boxes = [{"mol_id": 0, "node_ref": TARGET + ".m000"}]
    structures = [
        {
            "node_ref": TARGET + ".m000",
            "e_smiles_raw": "*c1ccccc1<sep><a>0:R</a>",
            "validation": {"valid": True},
        }
    ]
    result = deliver(ex, boxes, structures, blocked_instance_paths=blocked)
    member = result["payload"]["pathways"][0]["steps"][0]["products"][0]["variants"][0]
    assert member["assembly_status"] == "needs_review" and "assembled_molecule" not in member
    assert any(e.get("reason") == "reference_assignment_constraints_not_preserved" for e in result["errors"])
    enforce_reference_assignments(result, ex, s["exploration"])
    assert result["payload"]["status"] == "unclear"


def test_nonmatching_member_other_document_and_reactant_target_rejected():
    with pytest.raises(ValueError):
        register(
            assignments=[{"reference": "85", "substituents": {"R": "Cl"}, "evidence_text": "Wrong series"}]
        )
    with pytest.raises(ValueError):
        register(product_path="/pathways/0/steps/0/reactants/0")
    s = state()
    s["exploration"]["evidence"][1]["result"]["node_ref"] = "foreign/r1/p007.g000"
    with pytest.raises((ValueError, PermissionError, KeyError)):
        register(s)


def test_ignored_or_reselected_evidence_cannot_implicitly_apply_supplement():
    s, _ = register()
    s["exploration"]["applied_evidence_ids"] = []
    result = apply_reference_assignments(s["extraction"], s["exploration"])
    assert result["pathways"][0]["steps"][0]["products"][0]["variants"] == []
    assert any(
        e["code"] == "reference_evidence_not_applied" for e in s["exploration"]["reference_assignment_errors"]
    )


def test_second_conflicting_reference_record_requires_further_investigation():
    s, _ = register()
    with pytest.raises(ValueError, match="Conflicting"):
        register(
            s,
            assignments=[
                {"reference": "73", "substituents": {"R": "F"}, "evidence_text": "Conflicting source"}
            ],
        )


def test_reordered_or_replaced_product_is_not_patched_by_old_source_record():
    s, _ = register()
    ex = extraction()
    ex["pathways"][0]["steps"][0]["products"][0]["reference"] = "85–89"
    result = apply_reference_assignments(ex, s["exploration"])
    assert result["pathways"][0]["steps"][0]["products"][0]["variants"] == []
    assert any(
        e["code"] == "reference_target_changed" for e in s["exploration"]["reference_assignment_errors"]
    )


async def test_native_agent_can_discover_view_record_then_reextract_and_validate(figure_env):
    from types import MethodType

    from drugpaper.agents.figure.context import extraction_evidence
    from drugpaper.agents.figure.exploration import explore
    from drugpaper.persistence.models import NodeRow
    from tests.unit.figure.test_exploration import FINISH, harness

    agent, s, model = await harness(
        figure_env,
        [
            ("extract_figure", {}),
            ("find_reference_visuals", {"references": ["73", "74"], "terms": ["Preparation"]}),
            ("inspect_visual", {"node_id": "p000.b000"}),
            ("record_reference_assignments", args(evidence_ids=["e1"])),
            FINISH,
            ("extract_figure", {}),
            ("validate_result", {}),
            FINISH,
        ],
    )
    async with agent.services.db.session() as session:
        node = await session.get(NodeRow, ("doc_chem", 1, "p000.b000"))
        node.node_type = "scheme"
        node.page = 0
        node.text_norm = "Preparation of compounds 73–74"
        node.geometry = {"regions": [{"page": 0, "bbox": {"x0": 0, "y0": 0, "x1": 1, "y1": 1}}]}

    async def extract(self, state):
        payload = extraction()
        state["exploration"]["applied_evidence_ids"] = [
            e["id"] for e in extraction_evidence(state["exploration"])
        ]
        state["extraction"] = apply_reference_assignments(payload, state["exploration"])
        return state

    agent._structure_once = MethodType(extract, agent)
    await explore(agent, s)
    assert len(s["exploration"]["reference_assignments"]) == 1
    assert s["extraction"]["pathways"][0]["steps"][0]["products"][0]["variants"][0]["substituents"] == {
        "R": "Cl"
    }
    finishes = [
        e["result"]
        for e in s["exploration"]["steps"]
        if e.get("kind") == "tool" and e.get("name") == "finish"
    ]
    assert not finishes[0]["ok"] and finishes[1]["ok"]
    assert {"find_reference_visuals", "record_reference_assignments"} <= {
        t.name for t in model.requests[0].tools
    }


def test_successful_correction_clears_previous_block_without_discarding_source_record():
    s, _ = register()
    ex = extraction()
    ex["pathways"][0]["steps"][0]["products"][0]["variants"] = [
        {
            "reference": "73",
            "scaffold_mol_id": 0,
            "substituents": {"R": "F"},
            "evidence_text": "Old conflicting assignment",
        }
    ]
    ex = apply_reference_assignments(ex, s["exploration"])
    assert blocked_instance_paths(ex, s["exploration"])
    ex["pathways"][0]["steps"][0]["products"][0]["variants"][0]["substituents"]["R"] = "Cl"
    assert blocked_instance_paths(ex, s["exploration"]) == []
    assert s["exploration"]["reference_assignments"]


def test_distinct_members_cannot_be_materialized_as_copied_template_assignments():
    from drugpaper.agents.figure.reference_assignments import unproven_member_paths

    ex = extraction()
    p = ex["pathways"][0]["steps"][0]["products"][0]
    p["variants"] = [
        {
            "reference": ref,
            "scaffold_mol_id": 0,
            "substituents": {"R": "F"},
            "evidence_text": "Unverified candidate copied to paper labels",
        }
        for ref in ["73", "74"]
    ]
    assert len(unproven_member_paths(ex, [])) == 2
    assert len(blocked_instance_paths(ex, {})) == 2
    # Confirmation of co-identity is an evidence decision, not a ban on identical chemistry.
    records = [{"product_path": PATH, "assignments": [{"reference": "73"}, {"reference": "74"}]}]
    assert unproven_member_paths(ex, records) == []


def test_position_evidence_contract_keeps_unseen_dimensions_instead_of_deleting_them():
    s, _ = register(
        assignments=[
            {
                "reference": "73",
                "substituents": {"R": "Cl"},
                "position_definitions": {"substitution_site": "3-prime", "linker_length": "two"},
                "evidence_text": "Source explicitly defines these dimensions",
            }
        ]
    )
    out = apply_reference_assignments(s["extraction"], s["exploration"])
    member = out["pathways"][0]["steps"][0]["products"][0]["variants"][0]
    assert member["position_definitions"] == {"substitution_site": "3-prime", "linker_length": "two"}
    assert not supplement_errors(out, s["exploration"]["reference_assignments"])
