from scripts.verify_phase_5 import validate_table3
from tests.unit.figure.test_values_and_tables import table3


def test_live_table3_acceptance_rejects_wrong_value_and_duplicate_metric():
    from drugpaper.facts.table_processing import map_measurements

    measurements, _, _ = map_measurements(table3(), "doc", "doc/r1/p007.g001")
    payloads = [m.model_dump() for m in measurements]
    assert validate_table3(payloads)["status"] == "passed"
    payloads[0]["value"] = 500
    assert "solubility: value mismatch" in validate_table3(payloads)["errors"]
    payloads.append(payloads[1])
    assert any("expected one active" in e for e in validate_table3(payloads)["errors"])


def test_audit_rejects_empty_fallback_even_when_agent_says_ok():
    from scripts.audit_phase5_results import audit_figure

    result = audit_figure(
        {"node_ref": "doc/r1/table", "status": "ok", "image_type": "data_table", "extraction": {}}
    )
    assert result["machine_check"] == "failed" and result["acceptance"] == "failed"
    assert "empty extraction / error fallback" in result["reasons"]


def test_model_selfcheck_is_not_independent_content_acceptance():
    from scripts.audit_phase5_results import audit_figure

    result = audit_figure(
        {
            "node_ref": "doc/r1/table",
            "status": "ok",
            "image_type": "data_table",
            "extraction": table3().model_dump(),
            "review": {"items": []},
        }
    )
    assert result["acceptance"] == "not_verified"


def test_table1_cell_comparison_rejects_one_wrong_prime_or_uncertainty():
    import copy
    import json
    from pathlib import Path

    from scripts.audit_phase5_results import compare_table1

    gold = json.loads(Path("tests/fixtures/jmedchem_2c00949/p5_table1_gold.json").read_text())
    grid = {"rows": [[{"text": t} for t in row] for row in gold["rows"]]}
    assert compare_table1(grid, gold)["matched_cells"] == 80
    bad = copy.deepcopy(grid)
    bad["rows"][0][2]["text"] = "3′"
    bad["rows"][2][7]["text"] = "12.7 ± 2.5"
    result = compare_table1(bad, gold)
    assert result["status"] == "failed" and result["matched_cells"] == 78


def test_metadata_accepts_math_typography_but_rejects_wrong_concentration_or_association():
    from scripts.audit_phase5_results import compare_table_metadata

    gold = {
        "footnote_markers": {"a": "Blockade of the K+ channel at 10 μM."},
        "row_markers": ["a"],
    }
    grid = {
        "rows": [[{"text": "hERG", "markers": ["a"]}, {"text": "6"}]],
        "footnote_markers": {"a": r"Blockade of the K$^+$ channel at 10 $\mu$M."},
    }
    assert compare_table_metadata(grid, gold)["status"] == "passed"
    grid["footnote_markers"]["a"] = "Blockade of the K+ channel at 5 μM."
    assert compare_table_metadata(grid, gold)["status"] == "failed"
    grid["footnote_markers"]["a"] = gold["footnote_markers"]["a"]
    grid["rows"][0][0]["markers"] = []
    assert compare_table_metadata(grid, gold)["status"] == "failed"


def test_assay_concentration_accepts_latex_and_preserves_original_evidence():
    from drugpaper.facts.table_processing import conditions

    raw = r"Binding to human serum albumin at 5 $\mu$M."
    parsed = conditions(raw)
    assert parsed["compound_conc"] == "5 μM"
    assert parsed["text_raw"] == raw
