from __future__ import annotations

import pytest

from drugpaper.chem.normalize import canonical
from drugpaper.facts.labels import expand_range, normalize
from drugpaper.facts.resolver import resolve


def candidate(identity, smiles, source="ocsr", confidence=0.9):
    norm = canonical(smiles)
    return {
        "structure_id": identity,
        "source": source,
        "confidence": confidence,
        "validation": {"valid": True},
        "inchikey": norm.inchikey,
        "evidence": [{"node_id": "doc/r1/n", "role": "primary"}],
    }


def test_labels_ranges_alias_and_stereo_are_distinct():
    assert normalize("**compound 26**") == "26"
    assert expand_range("16−18") == ["16", "17", "18"]
    with pytest.raises(ValueError):
        expand_range("1-10000")
    data = resolve(
        "doc",
        claims=[{"label": "compound 26"}, {"label": "(S)-26"}, {"label": "UCM-1306"}],
        structures=[],
        text="26 [UCM-1306]",
    )
    by_label = {c["primary_label"]: c for c in data["compounds"]}
    assert set(by_label) == {"26", "(S)-26"}
    assert by_label["(S)-26"]["stereo_parent_id"] == by_label["26"]["compound_id"]
    assert by_label["26"]["aliases"] == ["UCM-1306"]


def test_priority_connectivity_stereo_conflict_and_scope():
    structures = [
        candidate("a", "CCO", "ocsr", 0.1),
        candidate("b", "CC", "markush_assembly"),
        candidate("c", "CCN", "manual"),
    ]
    data = resolve(
        "doc",
        claims=[{"label": "26", "structure_id": s["structure_id"]} for s in structures],
        structures=structures,
    )
    assert data["compounds"][0]["accepted_structure_id"] == "c"
    assert data["conflicts"][0]["type"] == "connectivity"
    scoped = resolve(
        "doc",
        claims=[{"label": "26"}, {"label": "3"}],
        structures=structures,
        existing=data["compounds"],
        scope=[data["compounds"][0]["compound_id"]],
    )
    assert [c["primary_label"] for c in scoped["compounds"]] == ["26"]
    stereo = [candidate("r", "N[C@H](C)O"), candidate("s", "N[C@@H](C)O")]
    data = resolve(
        "doc", claims=[{"label": "26", "structure_id": s["structure_id"]} for s in stereo], structures=stereo
    )
    assert data["conflicts"][0]["type"] == "stereochemistry"


def test_manual_lock_invalid_candidate_and_formula_mismatch():
    structures = [candidate("old", "CC"), candidate("new", "CCO", "manual")]
    existing = [
        {
            "compound_id": "cmp1",
            "document_id": "doc",
            "primary_label": "26",
            "accepted_structure_id": "old",
            "locked_fields": ["accepted_structure_id"],
        }
    ]
    data = resolve(
        "doc", claims=[{"label": "26", "structure_id": "new"}], structures=structures, existing=existing
    )
    assert data["compounds"][0]["accepted_structure_id"] == "old"
    assert any(c["type"] == "locked_structure" for c in data["conflicts"])
    structures[0]["validation"]["valid"] = False
    data = resolve("doc", claims=[{"label": "26", "structure_id": "old"}], structures=structures)
    assert data["compounds"][0]["accepted_structure_id"] is None
