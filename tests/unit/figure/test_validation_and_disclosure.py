import pytest

from drugpaper.agents.coordinator.reconcile import questions
from drugpaper.agents.figure.remediation import apply_review_fix, apply_text_layer
from drugpaper.agents.figure.report import FigureReport
from drugpaper.capabilities.router import CapabilityRouter, DisclosureContext
from drugpaper.facts.figure_models import ReviewResult
from drugpaper.validators.figure import check_all
from drugpaper.validators.figure.checklist import build, passed
from drugpaper.validators.figure.codes import CODES
from drugpaper.validators.figure.numeric import text_match


def codes(ctx):
    return {c.code for c in check_all(ctx)}


@pytest.mark.parametrize(
    "code,bad,good",
    [
        (
            "C_METRIC_UNMAPPED",
            {"measurements": [{"metric": "unmapped:P (cm/s)"}]},
            {"measurements": [{"metric": "pampa_pe"}]},
        ),
        (
            "C_PANEL_COUNT",
            {"expected_panels": 2, "extraction": {"panels": [{}]}},
            {"expected_panels": 1, "extraction": {"panels": [{}]}},
        ),
        (
            "C_CLASS_PRIOR",
            {"prior_type": "table", "image_type": "reported_structures"},
            {"prior_type": "table", "image_type": "data_table"},
        ),
        ("C_NO_BOX", {"expects_structures": True}, {"expects_structures": False}),
        (
            "C_OCSR_FAIL",
            {"structures": [{"validation": {"valid": False}}]},
            {"structures": [{"validation": {"valid": True}}]},
        ),
        (
            "C_UNREF_BOX",
            {"boxes": [{"mol_id": 0}], "extraction": {}},
            {"boxes": [{"mol_id": 0}], "extraction": {"molecules": [{"mol_id": 0}]}},
        ),
        ("C_BACKFILL_ERR", {"backfill_errors": ["bad"]}, {"backfill_errors": []}),
        (
            "C_LABEL_UNKNOWN",
            {"known_labels": ["26"], "extraction": {"molecules": [{"reference": "77"}]}},
            {"known_labels": ["26"], "extraction": {"molecules": [{"reference": "26"}]}},
        ),
        (
            "C_TEXT_LAYER_MISS",
            {"text_tokens": ["150"], "measurements": [{"value_raw": "50"}]},
            {"text_tokens": ["50"], "measurements": [{"value_raw": "50"}]},
        ),
        ("C_VALUE_PARSE", {"measurements": [{"value_raw": "???"}]}, {"measurements": [{"value_raw": "50"}]}),
        (
            "C_UNIT_CLASS",
            {"measurements": [{"value_raw": "5", "metric": "ec50", "unit": "h"}]},
            {"measurements": [{"value_raw": "5", "metric": "ec50", "unit": "μM"}]},
        ),
        (
            "C_RANGE_HARD",
            {"measurements": [{"value_raw": "101", "value": 101, "metric": "ppb"}]},
            {"measurements": [{"value_raw": "99", "value": 99, "metric": "ppb"}]},
        ),
        (
            "C_RANGE_SOFT",
            {"measurements": [{"value_raw": "100", "value_si": 100, "metric": "ec50"}]},
            {"measurements": [{"value_raw": "10", "value_si": 1e-5, "metric": "ec50"}]},
        ),
        (
            "C_MAGNITUDE",
            {"measurements": [{"value_raw": str(v), "value": v, "metric": "x"} for v in [1, 2, 1e6]]},
            {"measurements": [{"value_raw": str(v), "value": v, "metric": "x"} for v in [1, 2, 3]]},
        ),
        (
            "C_ROW_COUNT",
            {"expected_rows": 10, "extraction": {"rows": []}},
            {"expected_rows": 0, "extraction": {"rows": []}},
        ),
        (
            "C_AXIS_RESIDUAL",
            {
                "extraction": {
                    "x_axis": {},
                    "y_axis": {},
                    "calibration": {"x": {"residual": 0.1}, "y": {"residual": 0}},
                }
            },
            {
                "extraction": {
                    "x_axis": {},
                    "y_axis": {},
                    "calibration": {"x": {"residual": 0}, "y": {"residual": 0}},
                }
            },
        ),
        (
            "C_AXIS_SCALE",
            {"extraction": {"x_axis": {"scale": "log10", "ticks": [{"value": 0}]}, "y_axis": {}}},
            {"extraction": {"x_axis": {"scale": "log10", "ticks": [{"value": 1}]}, "y_axis": {}}},
        ),
        (
            "C_LEGEND_MISMATCH",
            {"extraction": {"x_axis": {}, "y_axis": {}, "legend_count": 2, "series": [{}]}},
            {"extraction": {"x_axis": {}, "y_axis": {}, "legend_count": 1, "series": [{}]}},
        ),
        (
            "C_OVERLAY_IOU_LOW",
            {"extraction": {"x_axis": {}, "y_axis": {}, "overlap_iou": 0.2}},
            {"extraction": {"x_axis": {}, "y_axis": {}, "overlap_iou": 0.8}},
        ),
        (
            "C_HEADER_SPAN",
            {"extraction": {"header_rows": [[{"text": "A"}, {"text": "B"}]], "rows": [[{"text": "1"}]]}},
            {"extraction": {"header_rows": [[{"text": "A"}]], "rows": [[{"text": "1"}]]}},
        ),
        (
            "C_ROUTE_TOPOLOGY",
            {"route_labels": ["1"], "extraction": {"relations": [{"source": "1", "target": "2"}]}},
            {"route_labels": ["1", "2"], "extraction": {"relations": [{"source": "1", "target": "2"}]}},
        ),
        (
            "C_ENTITY_UNTRACEABLE",
            {"source_text": ["compound 26"], "extraction": {"description": "compound 77"}},
            {"source_text": ["compound 26"], "extraction": {"description": "compound 26"}},
        ),
        (
            "C_CAPTION_MISMATCH",
            {"caption": "PK profile", "extraction": {}},
            {"caption": "PK profile", "extraction": {"x_axis": {"title": "Time"}}},
        ),
        ("C_STATUS_UNCLEAR", {"extraction": {"status": "unclear"}}, {"extraction": {"status": "ok"}}),
        (
            "C_REQUIRED_EMPTY",
            {"image_type": "molecular_docking", "extraction": {}},
            {"image_type": "molecular_docking", "extraction": {"residues": ["Asp1"]}},
        ),
    ],
)
def test_every_l1_check_positive_and_negative(code, bad, good):
    assert code in CODES and code in codes(bad)
    assert code not in codes(good)


def test_numeric_boundaries_and_checklist_uncertainty():
    assert not text_match("50", ["150", "50e-6", "0.50"])
    assert text_match("24 × 10−6", ["P: 24 × 10−6"])
    ctx = {
        "measurements": [{"value_raw": "50", "evidence": [{"field_path": "rows[0][1]"}]}],
        "text_tokens": ["50"],
    }
    selected, unchecked = build(ctx, [], budget=50)
    assert selected == [] and unchecked == []
    review = ReviewResult(items=[{"dimension": "numeric", "field_path": "a", "verdict": "correct"}])
    assert passed(review)
    assert not passed(review, [{"dimension": "numeric", "field_path": "b"}])
    assert not passed(ReviewResult(items=[{"dimension": "numeric", "field_path": "a", "verdict": "unsure"}]))


def test_safe_review_fix_unique_text_and_manual_lock():
    payload = {
        "rows": [[{"text": "150", "bbox": {"x0": 0.1, "y0": 0.1, "x1": 0.5, "y1": 0.5}, "source": "vlm"}]]
    }
    review = ReviewResult(
        items=[
            {
                "dimension": "numeric",
                "field_path": "rows[0][0].text",
                "verdict": "incorrect",
                "suggested_value": "50",
            }
        ]
    )
    changed = apply_review_fix(payload, review)
    assert changed["rows"][0][0]["text"] == "50" and payload["rows"][0][0]["text"] == "150"
    span = {"text": "50", "bbox": {"x0": 0.2, "y0": 0.2, "x1": 0.3, "y1": 0.3}}
    assert apply_text_layer(payload, ["rows[0][0]"], [span])["rows"][0][0]["source"] == "text_layer"
    with pytest.raises(ValueError):
        apply_text_layer(payload, ["rows[0][0]"], [span, span])
    payload["rows"][0][0]["source"] = "manual"
    with pytest.raises(PermissionError):
        apply_review_fix(payload, review)


def test_coordinator_conflict_questions_grouped_and_capped():
    reports = [
        FigureReport(
            report_id=f"r{i}",
            document_id="doc",
            node_ref=f"doc/r1/figure{i}",
            run_id="r",
            extraction={"molecules": [{"reference": "12a", "inchikey": key}]},
        )
        for i, key in enumerate(["AAAA", "BBBB"])
    ]
    result = questions(reports)
    assert set(result) == {"doc/r1/figure0", "doc/r1/figure1"}
    assert all("12a" in items[0] for items in result.values())
    assert len(questions(reports, max_questions=1)) == 1


async def test_progressive_action_selection_is_scoped_and_enum_only(molecule_env):
    _, _, bundle, _ = molecule_env
    disclosure = CapabilityRouter(bundle.index).preselect(
        DisclosureContext(
            audience="figure_agent",
            failed_checks=["C_AXIS_RESIDUAL"],
            invocations=["action_enum"],
            max_candidates=4,
            token_budget=800,
        )
    )
    names = {item.name for item in disclosure.items}
    assert names == {"recalibrate_axes", "reclassify", "give_up"}
    assert not names & {"table_extract", "molecule_detect", "run_code", "retranscribe_cells"}
    assert all(bundle.index.entries[name].invocation == "action_enum" for name in names)
    assert all(item.level in {"L0", "L1"} for item in disclosure.items)


def test_large_review_material_preserves_closed_paths_without_global_grid():
    from drugpaper.validators.figure.checklist import review_material

    data = {
        "rows": [[{"text": "50"}, {"text": "large-data-" + str(i)}] for i in range(10000)],
        "orientation": "properties_as_rows",
    }
    material = review_material(data, [{"field_path": "rows[9999][0]", "dimension": "numeric"}])
    assert material["field_counts"]["rows"] == 10000
    assert material["requested_field_values"]["rows[9999][0]"]["text"] == "50"
    assert "rows" not in material and "large-data-" not in str(material)


def test_review_cannot_replace_chart_axis_object_with_prose():
    from drugpaper.agents.figure.remediation import apply_review_fix
    from drugpaper.facts.figure_models import ReviewItem, ReviewResult

    payload = {
        "chart_type": "line",
        "x_axis": {"ticks": []},
        "y_axis": {"ticks": []},
        "plot_area": {"x0": 0, "y0": 0, "x1": 1, "y1": 1},
        "series": [],
    }
    review = ReviewResult(
        items=[
            ReviewItem(
                dimension="chart", field_path="x_axis", verdict="incorrect", suggested_value="linear axis"
            )
        ]
    )
    with pytest.raises(ValueError):
        apply_review_fix(payload, review)
    assert isinstance(payload["x_axis"], dict)


def test_text_verified_numbers_do_not_hide_missing_table_footnotes():
    from tests.unit.figure.test_values_and_tables import table3

    grid = table3().model_dump()
    grid["footnote_markers"] = {}
    selected, _ = build({"extraction": grid, "measurements": [], "text_tokens": []}, [])
    paths = {i["field_path"] for i in selected}
    assert {"header_rows", "footnote_markers", "subject_compound_label", "rows"} <= paths
    assert not passed(ReviewResult(items=[]), selected)
