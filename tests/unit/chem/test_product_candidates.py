import copy
import json
from pathlib import Path

import pytest
from rdkit import Chem

from drugpaper.chem.product_candidates import assemble, combinations, materialize_products

FIRST = "O=Cc1cccc(-c2ccccc2*)c1<sep><a>13:XCHYZ</a>"
SECOND = "*c1cc(*)c(-c2*cccc2OCF)cc1<sep><a>0:R</a><a>4:X</a><a>7:Y</a>"


def extraction():
    root = Path(__file__).resolve().parents[3]
    return json.loads((root / "samples/outputs/figure2-gpt-6-luna-structured.json").read_text())


def test_eight_assignments_form_six_full_molecules():
    data = extraction()
    e = data["pathways"][0]["steps"][0]["products"][0]
    combos, count = combinations(e["markush"])
    assert count == 8 and len(combos) == 6
    products = [assemble(FIRST, c["substituents"]) for c in combos]
    assert len({p["canonical_smiles"] for p in products}) == 6
    assert {p["formula"] for p in products} == {
        "C14H12O2",
        "C14H11FO2",
        "C14H10F2O2",
        "C14H12OS",
        "C14H11FOS",
        "C14H10F2OS",
    }


def test_products_only_assemble_and_unbounded_groups_are_local_failures():
    data = extraction()
    original = copy.deepcopy(data)
    steps = data["pathways"][0]["steps"]
    steps[0]["products"][0]["scaffold_esmiles"] = FIRST
    steps[2]["products"][0]["scaffold_esmiles"] = SECOND
    errors = materialize_products(data)
    first, second = (steps[i]["products"][0] for i in (0, 2))
    assert first["markush_assembly"]["completed_count"] == 6
    assert second["markush_assembly"]["candidate_count"] == 40
    assert second["markush_assembly"]["completed_count"] == 32
    assert second["markush_assembly"]["failed_count"] == 8
    assert len(errors) == 8
    assert {e["reason"] for e in errors} == {"unbounded_Alk_requires_explicit_definition"}
    assert all(e["field_path"].endswith(".markush.substitution_cases") for e in errors)
    for i, step in enumerate(steps):
        assert step["reactants"] == original["pathways"][0]["steps"][i]["reactants"]
    for endpoint in (first, second):
        assert endpoint["variants"] == []
        for c in endpoint["assembly_candidates"]:
            assert c["reference"] is None
            if c["status"] == "completed":
                mol = Chem.MolFromSmiles(c["assembled_molecule"]["canonical_smiles"])
                assert len(Chem.GetMolFrags(mol)) == 1
                assert all(a.GetAtomicNum() != 0 for a in mol.GetAtoms())


@pytest.mark.parametrize(
    "caption",
    [
        "O=Cc1ccccc1<sep><r>0:R</r>",
        "*c1ccccc1<sep><a>2:R</a>",
        "*c1ccccc1<sep>",
    ],
)
def test_inaccurate_attachment_annotations_fail_closed(caption):
    with pytest.raises(ValueError):
        assemble(caption, {"R": "COMe"})


def test_limit_is_not_silent_truncation():
    markush = extraction()["pathways"][0]["steps"][0]["products"][0]["markush"]
    with pytest.raises(ValueError, match="candidate_enumeration_limit"):
        combinations(markush, limit=2)
