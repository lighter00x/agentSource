from __future__ import annotations

import pytest

from drugpaper.chem.esmiles import assert_roundtrip, parse, split
from drugpaper.chem.formula import parse_hrms
from drugpaper.chem.markush import assemble_row
from drugpaper.chem.normalize import canonical
from drugpaper.chem.roles import infer_from_esmiles
from drugpaper.chem.splicer.adapter import substitute


def test_raw_split_preserves_multiple_separators_and_unicode():
    raw = " *CC<sep><a>0:R′</a><sep> 原文 "
    core, extension = split(raw)
    assert core == " *CC" and extension == "<a>0:R′</a><sep> 原文 "
    assert_roundtrip(raw, core + "<sep>" + extension)
    with pytest.raises(ValueError, match="V-C02"):
        assert_roundtrip(raw, raw.strip())
    parsed = parse("*CC<sep><a>0:R1</a>")
    assert parsed["groups"] == [{"type": "a", "value": "0:R1"}]
    assert parsed["markush"]


@pytest.mark.parametrize(
    "raw,role",
    [
        ("", "unknown"),
        ("CCO<sep>", "unknown"),
        ("*CC<sep>", "substituent"),
        ("*c1ccccc1<sep><a>0:R</a>", "scaffold"),
    ],
)
def test_migrated_conservative_roles(raw, role):
    assert infer_from_esmiles(raw) == role


@pytest.mark.parametrize("smiles", ["", "invalid", "C(C)(C)(C)(C)C"])
def test_invalid_structure_has_explicit_validation(smiles):
    result = canonical(smiles)
    assert not result.valid and result.error and result.inchikey is None


def test_normalization_and_formula():
    assert canonical("OCC").inchikey == canonical("CCO").inchikey
    assert canonical("CCO").formula == "C2H6O"
    assert parse_hrms("HRMS calcd for C14H14FNO2S [M+H]+ 280.0802") == {
        "formula": "C14H14FNO2S",
        "adduct": "[M+H]+",
    }
    assert parse_hrms("no formula") is None


def test_hydrogen_prime_and_single_row_assembly():
    assert substitute("*CC*<sep><a>0:R</a><a>3:R_prime</a>", {"R": "Cl", "R_prime": "Br"}).products == [
        "ClCCBr"
    ]
    assert substitute("C1CCCCC1*<sep><a>6:R</a>", {"R": "H"}).products == ["C1CCCCC1"]
    assert assemble_row("*CC*<sep><a>0:R</a><a>3:R′</a>", {"R": "Cl", "R′": "Br"}).products == ["ClCCBr"]
    assert assemble_row("CC", {"CHO position": "4"}).error_code == "positional_variable"
    assert assemble_row("CC", {"R": ["Cl", "Br"]}).error_code == "ambiguous_row"


@pytest.mark.parametrize("index", range(10))
def test_table1_positional_assembly_matches_manual_structures(index):
    import json
    from pathlib import Path

    gold = json.loads(Path("tests/fixtures/jmedchem_2c00949/p5_table1_molecule_gold.json").read_text())
    row = gold["molecules"][index]
    result = assemble_row("O=Cc1cccc(-c2ccccc2)c1<sep><r>0:CHO2</r><r>1:XHCHYZ</r>", row["values"])
    assert result.status == "completed" and len(result.products) == 1
    actual = canonical(result.products[0])
    assert actual.canonical_smiles == canonical(row["smiles"]).canonical_smiles
    assert actual.formula == row["formula"]


@pytest.mark.parametrize(
    "scaffold", ["CC", "c1ccccc1", "O=Cc1ccccc1", "O=Cc1ccc(-c2ccccc2Cl)cc1", "O=Cc1ccc(-c2ccccn2)cc1"]
)
def test_positional_assembly_rejects_unsupported_scaffolds(scaffold):
    result = assemble_row(scaffold, {"CHO position": "4", "XCHYZ position": "2′", "X": "O", "Y, Z": "H, F"})
    assert result.status != "completed" and result.products == []


@pytest.mark.parametrize(
    "key,value", [("CHO position", "1"), ("XCHYZ position", "2"), ("X", "N"), ("Y, Z", "F, Cl")]
)
def test_positional_assembly_rejects_unproven_assignments(key, value):
    row = {"CHO position": "4", "XCHYZ position": "2′", "X": "O", "Y, Z": "H, F"}
    row[key] = value
    result = assemble_row("O=Cc1ccc(-c2ccccc2)cc1", row)
    assert result.status == "failed" and result.products == []


@pytest.mark.parametrize("index", range(16))
def test_table2_atom_and_group_substitution_matches_manual_reference(index):
    import json
    from pathlib import Path

    row = json.loads(Path("tests/fixtures/jmedchem_2c00949/p5_table2_molecule_gold.json").read_text())[
        "molecules"
    ][index]
    result = assemble_row("*c1ccc(-c2*cccc2OCF)cc1<sep><a>0:R</a><a>6:Y</a><r>0:X</r>", row["values"])
    assert result.status == "completed" and len(result.products) == 1
    assert canonical(result.products[0]).canonical_smiles == canonical(row["smiles"]).canonical_smiles


@pytest.mark.parametrize(
    "key,value",
    [
        ("R", "Unknown"),
        ("Y", "O"),
        ("X", "2′-Cl"),
        ("X", "4-Cl"),
        ("X", "Cl"),
        ("X", "3′,5′-Cl"),
        ("X", "maybe"),
    ],
)
def test_labeled_ring_never_guesses_undefined_or_occupied_sites(key, value):
    row = {"R": "CHO", "X": "H", "Y": "CH"}
    row[key] = value
    result = assemble_row("*c1ccc(-c2*cccc2OCF)cc1<sep><a>0:R</a><a>6:Y</a><r>0:X</r>", row)
    assert result.status != "completed" and result.products == []
