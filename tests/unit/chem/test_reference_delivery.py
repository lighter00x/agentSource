import pytest
from rdkit import Chem

from drugpaper.facts.reference_delivery import resolve_references
from tests.unit.chem.test_pipeline_skills import molecule_env

__all__ = ["molecule_env"]


def candidate(raw="CS(=N)(=O)c1ccc(-c2ccccc2OCF)cc1", identity="s1"):
    return {
        "structure_id": identity,
        "node_ref": "doc/r1/scheme",
        "e_smiles_raw": raw,
        "validation": {"valid": True},
    }


def test_reference_binds_explicit_label_to_unique_document_structure():
    result = resolve_references(["26"], [{"label": "26", "structure_id": "s1"}], [candidate()])
    assert result["errors"] == []
    assert result["resolved"]["26"]["formula"] == "C14H14FNO2S"
    assert result["resolved"]["26"]["source_ref"] == "doc/r1/scheme"


def test_explicit_stereo_names_generate_distinct_verified_cip_enantiomers():
    result = resolve_references(["(R)-26", "(S)-26"], [{"label": "26", "structure_id": "s1"}], [candidate()])
    assert result["errors"] == []
    a, b = [result["resolved"][r] for r in ["(R)-26", "(S)-26"]]
    assert a["inchikey"] != b["inchikey"]
    for ref, data in result["resolved"].items():
        assert (
            Chem.FindMolChiralCenters(Chem.MolFromSmiles(data["canonical_smiles"]), includeUnassigned=True)[
                0
            ][1]
            == ref[1]
        )
        assert data["stereochemistry_from_explicit_label"] is True


@pytest.mark.parametrize("raw", ["CCO", "*CC", "bad", "CC(O)C(O)F"])
def test_stereo_label_never_guesses_missing_or_multiple_centers(raw):
    result = resolve_references(["(R)-26"], [{"label": "26", "structure_id": "s1"}], [candidate(raw)])
    assert result["resolved"] == {} and result["errors"]


def test_conflicting_connectivity_and_contradictory_stereo_are_rejected():
    claims = [{"label": "26", "structure_id": "s1"}, {"label": "26", "structure_id": "s2"}]
    assert resolve_references(["26"], claims, [candidate(), candidate("CCO", "s2")])["errors"]
    resolved = resolve_references(["(S)-26"], claims[:1], [candidate()])["resolved"]["(S)-26"]
    contradictory = resolve_references(
        ["(R)-26"], [{"label": "(R)-26", "structure_id": "s1"}], [candidate(resolved["canonical_smiles"])]
    )
    assert contradictory["resolved"] == {} and contradictory["errors"]


async def test_reference_tool_blocks_foreign_document(molecule_env):
    from drugpaper.gateway.base import ToolCall
    from drugpaper.tools.protocol import ToolContext

    _, _, bundle, _ = molecule_env
    # The Tool receives only the caller's identity, never an alternative user supplied in arguments.
    result = await bundle.tools.invoke(
        ToolContext(user_id="other", document_id="doc_chem", run_id="r", trace_id="t"),
        ToolCall(id="lookup", name="mol.lookup_references", arguments={"references": ["26"]}),
    )
    assert not result.ok and result.error == "E_FORBIDDEN"


@pytest.mark.parametrize("valid", [True, False])
def test_only_complete_explicit_3d_reference_binding_can_replace_2d_box_requirement(valid):
    from drugpaper.validators.figure.structure import check

    ctx = {
        "image_type": "molecular_docking",
        "expects_structures": True,
        "boxes": [],
        "extraction": {
            "representation": "3d_stick_models",
            "molecules": [{"reference": "(S)-26"}, {"reference": "(R)-26"}],
        },
        "reference_bindings": [{"reference": "(S)-26"}],
    }
    if valid:
        ctx["reference_bindings"].append({"reference": "(R)-26"})
    ctx["reference_3d_complete"] = valid
    assert any(c.code == "C_NO_BOX" for c in check(ctx)) is not valid
    ctx["backfill_errors"] = [{"code": "ambiguous"}]
    assert any(c.code == "C_NO_BOX" for c in check(ctx))
    ctx["backfill_errors"] = []
    ctx["reference_3d_complete"] = False
    assert any(c.code == "C_NO_BOX" for c in check(ctx))


@pytest.mark.parametrize("raw", ["CS(=O)c1ccccc1", "CS(=O)c1ccc(-c2ccccc2OCF)cc1", "CS(=N)(=O)c1ccccc1"])
def test_modern_cip_on_sulfur_centers_matches_final_output_not_legacy_assignment(raw):
    result = resolve_references(
        ["(R)-23", "(S)-23"], [{"label": "23", "structure_id": "s1"}], [candidate(raw)]
    )
    assert not result["errors"]
    for label, data in result["resolved"].items():
        mol = Chem.MolFromSmiles(data["canonical_smiles"])
        assert (
            Chem.FindMolChiralCenters(mol, includeUnassigned=True, useLegacyImplementation=False)[0][1]
            == label[1]
        )
