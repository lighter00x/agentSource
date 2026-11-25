from pathlib import Path

import pymupdf
import pytest

from drugpaper.domain.geometry import BBox, Region
from drugpaper.facts.figure_models import Cell, TableGrid
from drugpaper.facts.metric_dictionary import MetricDictionary
from drugpaper.facts.table_processing import expand_rows, headers, map_measurements, sar_rows
from drugpaper.facts.units import to_si, unit_class
from drugpaper.facts.value_parser import parse
from drugpaper.vision.text_layer import numeric_tokens, words_in_region


@pytest.mark.parametrize(
    "text,value,error,upper,status",
    [
        ("12.7 ± 2.4", 12.7, 2.4, None, "explicit"),
        ("24 × 10−6", 2.4e-5, None, None, "explicit"),
        ("79 (Kd = 1.56 × 10−4 M)", 79, None, None, "explicit"),
        ("<0.1", 0.1, None, None, "explicit"),
        ("nd", None, None, None, "not_determined"),
        ("3.2/4.1", 3.2, None, None, "explicit"),
        ("2–4", 2, None, 4, "explicit"),
        ("~3.5", 3.5, None, None, "explicit"),
        ("≈3.5", 3.5, None, None, "explicit"),
        ("1.2e-3", 0.0012, None, None, "explicit"),
        ("24 x 10^-6", 2.4e-5, None, None, "explicit"),
        ("12 ± 3 %", 12, 3, None, "explicit"),
        ("<=0.2", 0.2, None, None, "explicit"),
        ("≥100", 100, None, None, "explicit"),
        (">100", 100, None, None, "explicit"),
        ("n.d.", None, None, None, "not_determined"),
        ("NT", None, None, None, "not_determined"),
        ("N/A", None, None, None, "not_determined"),
        ("inactive", None, None, None, "inactive"),
        ("", None, None, None, "not_determined"),
        ("5 μM", 5, None, None, "explicit"),
        ("-3", -3, None, None, "explicit"),
        ("50%", 50, None, None, "explicit"),
        (".5", 0.5, None, None, "explicit"),
        ("0", 0, None, None, "explicit"),
        ("1e+3", 1000, None, None, "explicit"),
        ("5 +/- 1", 5, 1, None, "explicit"),
        ("4-2", 4, None, 2, "unparseable"),
        ("unreadable", None, None, None, "unparseable"),
        ("4 h", 4, None, None, "explicit"),
        ("50 nM", 50, None, None, "explicit"),
        ("3.2 / 4.1 %", 3.2, None, None, "explicit"),
        ("1.5 × 10⁻⁴", 0.00015, None, None, "explicit"),
        ("1.5 × 10−4 M", 0.00015, None, None, "explicit"),
        ("50 ng/mL", 50, None, None, "explicit"),
        ("≤.001", 0.001, None, None, "explicit"),
        ("10-20", 10, None, 20, "explicit"),
        ("−.5", -0.5, None, None, "explicit"),
        ("4 +− 2", 4, 2, None, "explicit"),
        ("3.5 ± 0.2 μM", 3.5, 0.2, None, "explicit"),
        ("1000", 1000, None, None, "explicit"),
        ("1.2E-3", 0.0012, None, None, "explicit"),
    ],
)
def test_all_value_formats(text, value, error, upper, status):
    result = parse(text)
    assert result.status == status
    assert result.value == pytest.approx(value) if value is not None else result.value is None
    assert result.error == error and result.value_upper == upper
    assert result.raw == text


@pytest.mark.parametrize(
    "unit,kind",
    [
        ("μM", "concentration"),
        ("nM", "concentration"),
        ("mL/min/kg", "clearance"),
        ("L/kg", "volume_per_mass"),
        ("ng/mL", "mass_concentration"),
        ("ng·h/mL", "auc"),
        ("cm/s", "permeability"),
        ("10^-6 cm/s", "permeability"),
        ("%", "percent"),
        ("h", "time"),
        (None, "dimensionless"),
    ],
)
def test_units(unit, kind):
    assert unit_class(unit) == kind


def test_pharmacy_unit_scale():
    assert to_si(10, "μM") == pytest.approx(1e-5)
    assert to_si(24, "10^-6 cm/s") == pytest.approx(2.4e-7)
    assert to_si(100, "%") == 100


def table3():
    names = [
        "solubility (μM)",
        "HSA binding (%)",
        "P (cm/s)",
        "hERG inhibition (%)",
        "mouse serum stability (%)",
        "liver homogenate stability (%)",
        "CYP2D6 (%)",
        "CYP34A (%)",
        "CYP1A2 (%)",
        "CYP2C9 (%)",
        "CYP2C19 (%)",
    ]
    values = [
        "50",
        "79 (Kd = 1.56 × 10−4 M)",
        "24 × 10−6",
        "21",
        "93",
        "65",
        "3 ± 1",
        "9 ± 1",
        "49 ± 2",
        "38 ± 3",
        "37 ± 2",
    ]
    markers = list("abcd eefffff".replace(" ", ""))
    return TableGrid(
        orientation="properties_as_rows",
        subject_compound_label="26",
        header_rows=[[Cell(text="property"), Cell(text="value")]],
        rows=[
            [Cell(text=name, markers=[marker]), Cell(text=value)]
            for name, value, marker in zip(names, values, markers, strict=True)
        ],
        footnote_markers={
            "a": "Maximum solubility measured by nephelometry.",
            "b": "Binding to human serum albumin determined at 5 μM.",
            "c": "Permeability in parallel artificial membrane permeability assay PAMPA.",
            "d": "Blockade of K+ channel current at 10 μM.",
            "e": "Remaining compound quantified after 4 and 24 h, respectively.",
            "f": "Percentage inhibition of CYP450 activity at 10 μM by fluorescence assays.",
        },
    )


def test_table3_all_eleven_measurements_and_secondary_kd():
    measurements, assays, unknown = map_measurements(table3(), "doc", "doc/r1/p007.table")
    primary = [m for m in measurements if m.metric != "kd"]
    assert len(primary) == 11 and len(measurements) == 12 and not unknown
    assert {m.metric for m in primary} == {
        "solubility",
        "ppb",
        "pampa_pe",
        "herg",
        "serum_stability",
        "liver_homogenate_stability",
        "cyp2d6_inhibition",
        "cyp3a4_inhibition",
        "cyp1a2_inhibition",
        "cyp2c9_inhibition",
        "cyp2c19_inhibition",
    }
    assert next(m for m in primary if m.metric == "cyp3a4_inhibition").normalization_warning == "typo"
    assert next(a for a in assays if a.assay_type == "herg").conditions["compound_conc"] == "10 μM"
    assert next(a for a in assays if a.assay_type == "serum_stability").conditions["time"] == ["4 h"]
    assert next(a for a in assays if a.assay_type == "liver_homogenate_stability").conditions["time"] == [
        "24 h"
    ]
    assert (
        next(a for a in assays if a.assay_type == "cyp3a4_inhibition").conditions["method"] == "fluorescence"
    )
    assert all(m.value_raw and m.evidence[0]["field_path"] for m in measurements)
    assert next(m for m in measurements if m.metric == "kd").value == pytest.approx(1.56e-4)


def test_sar_ten_rows_positions_error_and_spans():
    grid = TableGrid(
        orientation="compounds_as_rows",
        header_rows=[
            [Cell(text="Compound"), Cell(text="CHO position"), Cell(text="X"), Cell(text="EC50 (μM)")]
        ],
        rows=[
            [Cell(text=str(i)), Cell(text="4"), Cell(text="O"), Cell(text="12.7 ± 2.4")] for i in range(1, 11)
        ],
    )
    assert len(sar_rows(grid)) == 10 and sar_rows(grid)[0]["values"] == {"CHO position": "4", "X": "O"}
    measurements, _, _ = map_measurements(grid, "doc", "doc/r1/table")
    assert len(measurements) == 10 and all(m.error == 2.4 for m in measurements)
    span = TableGrid(header_rows=[[Cell(text="Effect", col_span=2)], [Cell(text="A"), Cell(text="B")]])
    assert headers(span) == ["Effect A", "Effect B"]
    assert len(expand_rows(span.header_rows)[0]) == 2


def test_text_layer_real_table3_and_scan_empty():
    pdf = next(Path("tests/fixtures/jmedchem_2c00949").rglob("*origin.pdf")).read_bytes()
    words = words_in_region(pdf, Region(page=7, bbox=BBox(x0=0, y0=0, x1=1, y1=1)))
    tokens = numeric_tokens(words)
    joined = " ".join(tokens)
    for value in ("50", "79", "21", "93", "65", "49", "38", "37"):
        assert value in joined
    assert "24e-6" in joined.replace(" ", "")
    with pymupdf.open() as doc:
        doc.new_page()
        blank = doc.tobytes()
    assert words_in_region(blank, Region(page=0, bbox=BBox(x0=0, y0=0, x1=1, y1=1))) == []


def test_metric_unknown_fuzzy_and_joint_rule():
    dictionary = MetricDictionary()
    assert dictionary.match("P (cm/s)", "PAMPA")["id"] == "pampa_pe"
    assert dictionary.match("EC5O")["normalization_warning"]
    assert dictionary.match("unexpected very exotic endpoint")["id"].startswith("unmapped:")


def test_real_empty_middle_bbox_does_not_destroy_other_nodes():
    from drugpaper.parsers.mineru_legacy.schemas import load_middle

    raw = {
        "pdf_info": [
            {
                "page_idx": 0,
                "para_blocks": [
                    {"type": "text", "bbox": [], "lines": []},
                    {"type": "text", "bbox": [], "lines": [{"bbox": [1, 2, 3, 4], "spans": []}]},
                ],
            }
        ]
    }
    middle = load_middle(raw)
    assert len(middle.pdf_info[0].para_blocks) == 1
    assert middle.pdf_info[0].para_blocks[0].bbox == (1, 2, 3, 4)
    assert raw["pdf_info"][0]["para_blocks"][0]["bbox"] == []
    assert len(middle.model_extra["repaired_geometry_paths"]) == 2


def test_markdown_math_transcription_becomes_real_cells_without_model_calls():
    from drugpaper.facts.table_transcription import canonicalize

    grid = canonicalize(
        TableGrid(
            title="Table 3\n| Property | Value |\n| :--- | :--- |\n| HSA binding (%)$^b$ | 79 ($K_d = 1.56 \\times 10^{-4}$ M) |\n| P (cm/s)$^c$ | $24 \\times 10^{-6}$ |\n\n$^b$Binding at 5 $\\mu$M. $^c$PAMPA."
        )
    )
    assert len(grid.rows) == 2 and grid.title == "Table 3"
    assert grid.rows[0][0].markers == ["b"] and "PAMPA" in grid.footnote_markers["c"]
    assert parse(grid.rows[0][1].text).secondary[0]["value"] == pytest.approx(1.56e-4)
    assert parse(grid.rows[1][1].text).value == pytest.approx(24e-6)
    assert canonicalize(TableGrid()).status == "unclear"


def test_original_table1_endpoints_are_not_sar_variables():
    import json

    gold = json.loads(Path("tests/fixtures/jmedchem_2c00949/p5_table1_gold.json").read_text())
    grid = TableGrid(
        orientation="compounds_as_rows",
        header_rows=[[Cell(text=t) for t in gold["header"]]],
        rows=[[Cell(text=t) for t in row] for row in gold["rows"]],
    )
    rows = sar_rows(grid)
    assert len(rows) == 10 and len(rows[0]["values"]) == 4
    measurements, _, _ = map_measurements(grid, "doc", "doc/r1/p003.g000")
    assert len(measurements) == 30
    assert {m.metric for m in measurements} == {"ec50", "pct_max_increase", "da_emax_potentiation"}
    ec50 = {m.subject_label: m for m in measurements if m.metric == "ec50"}
    assert ec50["3"].value == 12.7 and ec50["3"].error == 2.4
    assert ec50["10"].value == 21.8 and ec50["10"].error == 3.6
    assert ec50["1"].status == "not_determined"


def test_compact_table_sparse_metadata_preserves_full_contract():
    from drugpaper.facts.table_transcription import canonicalize, from_transcription

    wire = {
        "headers": ["Compound", "R", "EC50 (μM)"],
        "rows": [["1", "", "12.7 ± 2.4"]],
        "orientation": "unknown",
        "scaffold_mol_id": 0,
        "cell_details": [
            {"row": 0, "column": 1, "mol_id": 2, "status": "unclear"},
            {"section": "header", "row": 0, "column": 2, "markers": ["a"]},
        ],
        "footnote_markers": {"a": "10 μM; mean ± SEM"},
    }
    grid = canonicalize(from_transcription(wire))
    assert grid.orientation == "compounds_as_rows"
    assert grid.rows[0][1].mol_id == 2 and grid.rows[0][1].status == "unclear"
    assert grid.header_rows[0][2].markers == ["a"] and grid.scaffold_mol_id == 0
    assert grid.footnote_markers["a"] == "10 μM; mean ± SEM"
    wire["cell_details"].append({"row": 9, "column": 0})
    with pytest.raises(ValueError, match="outside"):
        from_transcription(wire)
