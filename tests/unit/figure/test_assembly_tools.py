"""Exercise owned assembly tools and corrections through the same deterministic delivery path."""

import copy
from types import MethodType, SimpleNamespace

import pytest

from drugpaper.agents.figure.assembly_tools import apply_assembly, inspect_assembly, try_assembly
from drugpaper.chem.assembly import assemble_explicit
from drugpaper.chem.normalize import canonical
from drugpaper.facts.molecular_delivery import deliver

SCAFFOLD = "O=Cc1cccc(-c2ccccc2*)c1<sep><a>13:XCHYZ</a>"
BIARYL = "*c1cc(*)c(-c2*cccc2OCF)cc1<sep><a>0:R</a><a>4:X</a><a>7:Y</a>"


def materials(scaffold="*c1ccccc1<sep><a>0:R</a>"):
    ref = "doc/r1/fig"
    return SimpleNamespace(
        target={
            "ref": ref,
            "children": [
                {"ref": ref + ".m000", "status": "active", "attributes": {"reading_index": 0}},
                {"ref": ref + ".m001", "status": "removed", "attributes": {"reading_index": 1}},
            ],
            "structures": [
                {
                    "node_ref": ref + ".m000",
                    "structure_id": "s0",
                    "e_smiles_raw": scaffold,
                    "validation": {"valid": True},
                },
                {
                    "node_ref": "doc/r1/other.m000",
                    "structure_id": "foreign",
                    "e_smiles_raw": "CCO",
                    "validation": {"valid": True},
                },
            ],
        }
    )


def extraction(group="OCH(H)F"):
    return {
        "compound_instances": [
            {
                "reference": "sample",
                "scaffold_mol_id": 0,
                "substituents": {"R": group},
                "evidence_text": "Printed assignment",
            }
        ]
    }


def log():
    return {
        "evidence": [{"id": "e1", "tool": "inspect_visual"}, {"id": "preview", "tool": "preview_pages"}],
        "applied_evidence_ids": ["e1"],
    }


def trial(m=None, ex=None, ledger=None, **change):
    m = m or materials()
    ex = ex or extraction()
    ledger = ledger if ledger is not None else log()
    result = try_assembly(m, ex, ledger, {"items": [{"instance_path": "/compound_instances/0", **change}]})
    return result["results"][0], ledger


def test_inventory_and_closed_alias_lookup_exclude_foreign_and_removed_structures():
    out = inspect_assembly(materials(), {"group_queries": ["OCH(H)F", "Alk", "3′-Cl"]})
    assert [s["mol_id"] for s in out["inventory"]["structures"]] == [0]
    assert out["groups"][0] == {"input": "OCH(H)F", "normalized": "OCH2F", "supported": True}
    assert not out["groups"][1]["supported"]
    assert out["groups"][2]["normalized"] == "3′-Cl"
    assert "e_smiles_raw" not in str(out)


def test_trial_does_not_mutate_then_apply_normalizes_and_backfill_recomputes():
    m = materials()
    ex = extraction()
    before = copy.deepcopy(ex)
    ledger = log()
    result, _ = trial(m, ex, ledger)
    assert result["status"] == "completed"
    assert ex == before
    apply_assembly(m, ex, ledger, {"attempt_ids": [result["attempt_id"]]})
    assert ex["compound_instances"][0]["substituents"] == {"R": "OCH2F"}
    assert "assembled_molecule" not in ex["compound_instances"][0]
    out = deliver(ex, [{"mol_id": 0, "node_ref": "doc/r1/fig.m000"}], m.target["structures"][:1])
    product = out["payload"]["compound_instances"][0]["assembled_molecule"]
    assert product["canonical_smiles"] == result["assembled_molecule"]["canonical_smiles"]


def test_local_trial_does_not_report_unrelated_detected_molecules_as_its_error():
    m = materials()
    m.target["children"].append(
        {"ref": "doc/r1/fig.m002", "status": "active", "attributes": {"reading_index": 2}}
    )
    m.target["structures"].append(
        {
            "node_ref": "doc/r1/fig.m002",
            "structure_id": "s2",
            "e_smiles_raw": "CCO",
            "validation": {"valid": True},
        }
    )
    result, _ = trial(m, extraction("Alk"))
    assert result["status"] == "failed"
    assert result["error"] != "unresolved_detected_box"
    assert all(e["code"] != "unresolved_detected_box" for e in result.get("errors", []))
    good, _ = trial(m)
    assert good["status"] == "completed" and good["errors"] == []


@pytest.mark.parametrize(
    "change",
    [
        {"substituents": {"R": "Cl"}},
        {"substituents": {"R": "Cl"}, "evidence_ids": ["preview"], "note": "Guess"},
        {"substituents": {"R": "Cl"}, "evidence_ids": ["invented"], "note": "Guess"},
        {"substituents": {"R": 99}, "evidence_ids": ["e1"], "note": "Foreign fragment"},
        {"substituents": {"R": "CCO"}, "evidence_ids": ["e1"], "note": "Model supplied SMILES"},
    ],
)
def test_changes_without_valid_evidence_or_owned_chemistry_fail(change):
    result, _ = trial(**change)
    assert result["status"] == "failed"
    assert "assembled_molecule" not in result


def test_semantic_change_with_real_applied_evidence_and_invalid_attempt_application():
    result, ledger = trial(substituents={"R": "Cl"}, evidence_ids=["e1"], note="Read printed Cl")
    assert result["status"] == "completed"
    bad, _ = trial(ledger=ledger, substituents={"R": "Alk"}, evidence_ids=["e1"], note="Still unknown")
    ex = extraction()
    original = copy.deepcopy(ex)
    with pytest.raises(ValueError, match="completed"):
        apply_assembly(materials(), ex, ledger, {"attempt_ids": [result["attempt_id"], bad["attempt_id"]]})
    assert ex == original  # Whole batch is checked before any mutation.


@pytest.mark.parametrize("changed", ["recognition", "instance", "evidence"])
def test_stale_trials_cannot_be_applied(changed):
    m = materials()
    ex = extraction()
    ledger = log()
    r, _ = trial(m, ex, ledger)
    if changed == "recognition":
        m.target["structures"][0]["e_smiles_raw"] = "*CC<sep><a>0:R</a>"
    elif changed == "instance":
        ex["compound_instances"][0]["reference"] = "other"
    else:
        ledger["applied_evidence_ids"] = []
        ledger["assembly_attempts"][0]["evidence_ids"] = ["e1"]
    with pytest.raises(ValueError):
        apply_assembly(m, ex, ledger, {"attempt_ids": [r["attempt_id"]]})


def test_known_position_gap_cannot_be_bypassed_by_fixing_abbreviation():
    ex = extraction()
    ex["information_gaps"] = [
        {"category": "unsupported_representation", "field_paths": ["/compound_instances"]}
    ]
    r, _ = trial(ex=ex)
    assert r["status"] == "failed" and "representation gap" in r["error"]


def test_explicit_positions_produce_distinct_molecules_from_same_recognized_scaffold():
    ex = {
        "compound_instances": [
            {
                "reference": "sample",
                "scaffold_mol_id": 0,
                "substituents": {"XCHYZ": "OCH(H)F"},
                "evidence_text": "Printed group",
            }
        ]
    }
    products = []
    for position in ["2'", "3'", "4'"]:
        r, ledger = trial(
            materials(SCAFFOLD),
            ex,
            position_definitions={"CHO position": "4", "XCHYZ position": position, "X": "O", "Y, Z": "H, F"},
            evidence_ids=["e1"],
            note="Explicit table positions",
        )
        assert r["status"] == "completed", r
        assert r["provenance"]["bindings"][0]["node_ref"] == "doc/r1/fig.m000"
        products.append(r["assembled_molecule"]["canonical_smiles"])
    assert len(set(products)) == 3
    assert products[0] == canonical("O=Cc1ccc(-c2ccccc2OCF)cc1").canonical_smiles


def test_positional_assignment_conflict_and_unsupported_scaffold_fail():
    with pytest.raises(ValueError, match="conflict"):
        assemble_explicit(
            SCAFFOLD,
            {"XCHYZ": "OCHF2"},
            {"CHO position": "4", "XCHYZ position": "2'", "X": "O", "Y, Z": "H, F"},
        )
    with pytest.raises(ValueError):
        assemble_explicit(
            "*CC<sep><a>0:XCHYZ</a>",
            {"XCHYZ": "OCH2F"},
            {"CHO position": "4", "XCHYZ position": "2'", "X": "O", "Y, Z": "H, F"},
        )


def test_ring_position_prefix_is_preserved_and_selects_different_products():
    products = []
    for x in ["H", "3′-Cl", "2-Cl"]:
        product, method = assemble_explicit(BIARYL, {"R": "SO(NH)Me", "X": x, "Y": "CH"})
        products.append(product)
        if x != "H":
            assert method == "explicit_labeled_ring_position"
    assert len(set(products)) == 3


async def test_native_agent_can_trial_apply_and_must_validate_again(figure_env):
    from drugpaper.agents.figure.exploration import explore
    from tests.unit.figure.test_exploration import FINISH, harness

    agent, state, model = await harness(
        figure_env,
        [
            ("extract_figure", {}),
            ("validate_result", {}),
            ("inspect_assembly", {}),
            ("try_assembly", {"items": [{"instance_path": "/compound_instances/0"}]}),
            ("apply_assembly", {"attempt_ids": ["a1"]}),
            FINISH,
            ("validate_result", {}),
            FINISH,
        ],
    )
    bundle, _ = await agent._ctx(state)
    m = materials()
    m.images = []
    ctx = SimpleNamespace(materials=m)

    async def context(self, state):
        return bundle, ctx

    async def extract(self, state):
        state["extraction"] = {"status": "ok", **extraction()}
        return state

    async def post(self, state):
        state["backfilled_extraction"] = state["extraction"].copy()
        fixed = state["extraction"]["compound_instances"][0]["substituents"] == {"R": "OCH2F"}
        state["material"]["backfill_errors"] = (
            []
            if fixed
            else [
                {
                    "code": "compound_instance_failed",
                    "field_path": "$.compound_instances[0]",
                    "reason": "Substituent requires normalization",
                }
            ]
        )
        return state

    agent._ctx = MethodType(context, agent)
    agent._structure_once = MethodType(extract, agent)
    agent._post = MethodType(post, agent)
    await explore(agent, state)
    finished = [
        s["result"]
        for s in state["exploration"]["steps"]
        if s.get("kind") == "tool" and s.get("name") == "finish"
    ]
    assert not finished[0]["ok"] and finished[1]["ok"]
    assert state["extraction"]["compound_instances"][0]["substituents"] == {"R": "OCH2F"}
    assert state["exploration"]["assembly_attempts"][0]["applied"]
    tools = {"inspect_assembly", "try_assembly", "apply_assembly"}
    assert not tools & {t.name for t in model.requests[0].tools}
    assert tools <= {t.name for t in model.requests[2].tools}
    assert not tools & {t.name for t in model.requests[-1].tools}


@pytest.mark.parametrize(
    "path",
    [
        "/compound_instances/00",
        "/pathways/0/steps/0/reactants/0/variants/0",
        "/molecules/0",
        "/compound_instances/0/../../molecules",
    ],
)
def test_repair_path_cannot_escape_product_instance_scope(path):
    out = try_assembly(materials(), extraction(), log(), {"items": [{"instance_path": path}]})
    assert out["results"][0]["status"] == "failed"


def test_batch_budget_and_removed_fragment_are_rejected():
    r, _ = trial(substituents={"R": 1}, evidence_ids=["e1"], note="Removed fragment")
    assert r["status"] == "failed"
    ledger = log()
    ledger["assembly_attempts"] = [{}] * 32
    with pytest.raises(ValueError, match="budget"):
        try_assembly(
            materials(), extraction(), ledger, {"items": [{"instance_path": "/compound_instances/0"}]}
        )


def test_attachment_sites_expose_labels_and_validate_explicit_selection():
    from drugpaper.agents.figure.assembly_tools import inspect_attachment_sites

    mat = materials(BIARYL)
    ext = {
        "pathways": [
            {
                "steps": [
                    {
                        "products": [
                            {
                                "variants": [
                                    {
                                        "scaffold_mol_id": 0,
                                        "substituents": {"R": "Me", "X": "H", "Y": "H"},
                                        "attachments": ["R"],
                                    },
                                    {
                                        "scaffold_mol_id": 0,
                                        "substituents": {"R": "Me", "X": "H", "Y": "H"},
                                        "attachments": ["R", "X", "Y", "Z"],
                                    },
                                    {
                                        "scaffold_mol_id": 0,
                                        "substituents": {"R": "Me", "X": "H", "Y": "H"},
                                        "attachments": ["R", "X", "Y"],
                                    },
                                ]
                            }
                        ]
                    }
                ]
            }
        ]
    }
    out = inspect_attachment_sites(mat, ext, {})
    site = out["sites"][0]
    assert site["attachment_labels"] == ["R", "X", "Y"]
    assert [row["status"] for row in site["instances"]] == [
        "incomplete_attachments",
        "invalid_attachments",
        "ok",
    ]
    assert site["instances"][0]["missing_labels"] == ["X", "Y"]
    assert site["instances"][1]["unexpected_keys"] == ["Z"]


def test_attachment_sites_mark_scaffold_without_replaceable_labels():
    from drugpaper.agents.figure.assembly_tools import inspect_attachment_sites

    mat = materials("COc1ccc(-c2ccc(C=O)cc2)cc1<sep>")
    ext = {
        "compound_instances": [
            {"scaffold_mol_id": 0, "substituents": {"XCHYZ": "OCH2F"}, "attachments": ["XCHYZ"]}
        ]
    }
    out = inspect_attachment_sites(mat, ext, {})
    assert out["sites"][0]["status"] == "no_attachment_sites"
    assert out["sites"][0]["instances"][0]["status"] == "no_attachment_sites"
