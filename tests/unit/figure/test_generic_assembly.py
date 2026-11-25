"""Metamorphic checks across labels, cores and side chains, independent of the sample paper."""

import copy

import pytest
from rdkit import Chem

from drugpaper.agents.figure.assembly_tools import inspect_assembly
from drugpaper.chem.assembly import assemble_explicit
from drugpaper.chem.assembly_inputs import project_inputs
from drugpaper.facts.molecular_delivery import deliver
from tests.unit.figure.test_assembly_tools import materials
from tests.unit.figure.test_compound_instances import scene


@pytest.mark.parametrize(
    "caption,groups,expected",
    [
        ("*CC*<sep><a>0:EndA</a><a>3:EndB</a>", {"EndA": "Cl", "EndB": "Br"}, "ClCCBr"),
        ("*C1CC1<sep><a>0:G7</a>", {"G7": "Me"}, "CC1CC1"),
        ("*c1ccncc1<sep><a>0:Side9</a>", {"Side9": "COOEt"}, "CCOC(=O)c1ccncc1"),
    ],
)
def test_arbitrary_labels_and_non_biaryl_scaffolds(caption, groups, expected):
    before = copy.deepcopy(groups)
    product, method = assemble_explicit(caption, groups)
    assert product == Chem.MolToSmiles(Chem.MolFromSmiles(expected))
    assert method == "explicit_atom_attachment"
    assert groups == before


@pytest.mark.parametrize("value,expected", [("O", "c1ccoc1"), ("S", "c1ccsc1"), ("NH", "c1cc[nH]c1")])
def test_aromatic_site_replacement_is_not_limited_to_pyridine(value, expected):
    product, _ = assemble_explicit("c1cc*c1<sep><a>3:Het7</a>", {"Het7": value})
    assert product == Chem.MolToSmiles(Chem.MolFromSmiles(expected))


def test_aromaticity_failure_never_produces_a_product():
    with pytest.raises((ValueError, RuntimeError)):
        assemble_explicit("c1cc*c1<sep><a>3:Het7</a>", {"Het7": "N"})


def test_ring_locants_use_graph_roles_and_allow_other_side_chains_and_halogen():
    caption = "*c1cc(*)c(-c2*cccc2CC)cc1<sep><a>0:G7</a><a>4:Site4</a><a>7:Het12</a>"
    groups = {"G7": "COOEt", "Site4": "3′-Br", "Het12": "N"}
    before = copy.deepcopy(groups)
    product, method = assemble_explicit(caption, groups)
    assert product == Chem.MolToSmiles(Chem.MolFromSmiles("CCOC(=O)c1ccc(-c2nccc(Br)c2CC)cc1"))
    assert method == "explicit_labeled_ring_position"
    assert groups == before
    # Relabelling cannot change chemistry.
    canonical_roles = caption.replace(":G7<", ":R<").replace(":Site4<", ":X<").replace(":Het12<", ":Y<")
    assert assemble_explicit(canonical_roles, {"R": "COOEt", "X": "3′-Br", "Y": "N"})[0] == product


def test_missing_numbering_landmark_is_not_guessed():
    caption = "*c1cc(*)c(-c2*cccc2)cc1<sep><a>0:G7</a><a>4:Site4</a><a>7:Het12</a>"
    with pytest.raises(ValueError):
        assemble_explicit(caption, {"G7": "Me", "Site4": "3′-Br", "Het12": "N"})


def test_selector_is_authoritative_for_unrelated_variable_names():
    groups = {"Ra": "Et", "COORa": "COOEt", "unselected_note": "Alk"}
    value = project_inputs(["COORa"], groups, attachments=["COORa"])
    assert value["substituents"] == {"COORa": "COOEt"}
    assert value["source_substituents"] == groups


def test_real_delivery_uses_generic_scaffold_and_preserves_selector():
    boxes, structures = scene("*c1ccncc1<sep><a>0:COORa</a>")
    payload = {
        "compound_instances": [
            {
                "reference": "L-a",
                "scaffold_mol_id": 0,
                "substituents": {"Ra": "Et", "COORa": "COOEt"},
                "attachments": ["COORa"],
                "evidence_text": "Explicit local assignment in the synthetic test fixture",
            }
        ]
    }
    original = copy.deepcopy(payload)
    result = deliver(payload, boxes[:1], structures[:1])
    member = result["payload"]["compound_instances"][0]
    assert member["assembly_status"] == "completed", result["errors"]
    assert member["assembled_molecule"]["canonical_smiles"] == "CCOC(=O)c1ccncc1"
    assert member["attachments"] == ["COORa"]
    assert member["substituents"] == original["compound_instances"][0]["substituents"]
    assert payload == original


def test_capabilities_do_not_impose_sample_position_field_names():
    result = inspect_assembly(materials(), {})
    assert "required_keys" not in result["position_contract"]
    assert any(c["name"] == "aromatic_atom_replacement" for c in result["capabilities"])
