"""Regression tests for the MolParser-derived E-SMILES splicing module."""

import pytest

from drugpaper.chem.splicer.vendor import (
    Translator,
    substitute_markush,
)

parse_caption = Translator.parse_caption

SCAFFOLD = "C1CCCCC1*<sep><a>6:R</a>"


def test_atom_dummy_substitution_with_abbreviation():
    assert substitute_markush(SCAFFOLD, {"R": "Me"}) == "CC1CCCCC1"


def test_fragment_dummy_controls_attachment_end():
    assert substitute_markush(SCAFFOLD, {"R": "OCC*"}) == "OCCC1CCCCC1"


def test_plain_fragment_esmiles_separator_is_removed_before_substitution():
    assert substitute_markush(SCAFFOLD, {"R": "OC<sep>"}) == "COC1CCCCC1"


def test_multiple_definition_values_expand_and_sort_products():
    assert substitute_markush(SCAFFOLD, {"R": ["CF3", "OMe"]}) == [
        "COC1CCCCC1",
        "FC(F)(F)C1CCCCC1",
    ]


def test_ring_group_enumeration_deduplicates_symmetric_positions():
    assert substitute_markush("c1ccccc1<sep><r>0:R[1]</r>", {"R1": "Cl"}) == "Clc1ccccc1"


def test_ring_multiplicity_enumerates_distinct_positional_isomers():
    assert substitute_markush("c1ccccc1<sep><r>0:R?2</r>", {"R": "Cl"}) == [
        "Clc1ccc(Cl)cc1",
        "Clc1cccc(Cl)c1",
        "Clc1ccccc1Cl",
    ]


def test_multiple_atom_groups_are_spliced_into_one_product():
    assert (
        substitute_markush(
            "*CC*<sep><a>0:R1</a><a>3:R2</a>",
            {"R1": "Cl", "R2": "Br"},
        )
        == "ClCCBr"
    )


def test_carbon_chain_repetition_expands_without_a_definition():
    assert substitute_markush("*C*<sep><a>1:CH2?3</a>", {}) == "*CCCC"


def test_precompat_substructure_is_recursively_substituted_and_preserved():
    assert (
        substitute_markush(
            "CC<sep><s>**<sep><a>0:L[3]</a><a>1:R[3]</a>|Sg:n|</s>",
            {"L3": "Cl", "R3": "Br"},
        )
        == "CC<sep><s>ClBr<sep>|Sg:n|</s>"
    )


def test_missing_definition_is_explicit_error():
    with pytest.raises(ValueError, match="No Markush definition found"):
        substitute_markush(SCAFFOLD, {})


def test_output_limit_prevents_combinatorial_expansion():
    with pytest.raises(ValueError, match="max_outputs=1"):
        substitute_markush(
            "c1ccccc1<sep><r>0:R</r>",
            {"R": ["Cl", "Br"]},
            max_outputs=1,
        )


def test_caption_parser_is_exposed_with_core_api():
    parsed = parse_caption(SCAFFOLD, return_mol=True)

    assert parsed is not None
    molecule, groups, extension = parsed
    assert molecule.GetNumAtoms() == 7
    assert groups == "<a>6:R</a>"
    assert extension == ""
