from __future__ import annotations

import re
from typing import Any

from pint.errors import PintError

from drugpaper.docsrc.textnorm import strip_latex
from drugpaper.domain.ids import new_id
from drugpaper.facts.figure_models import Assay, Cell, Measurement, TableGrid
from drugpaper.facts.metric_dictionary import MetricDictionary
from drugpaper.facts.units import to_si, unit_class
from drugpaper.facts.value_parser import parse


def expand_rows(rows: list[list[Cell]]) -> list[list[Cell]]:
    placed: dict[tuple[int, int], Cell] = {}
    width = 0
    for r, row in enumerate(rows):
        column = 0
        for cell in row:
            while (r, column) in placed:
                column += 1
            for dr in range(cell.row_span):
                for dc in range(cell.col_span):
                    if (r + dr, column + dc) in placed:
                        raise ValueError("overlapping cell spans")
                    placed[r + dr, column + dc] = cell
            column += cell.col_span
            width = max(width, column)
    return [[placed.get((r, c), Cell(status="missing")) for c in range(width)] for r in range(len(rows))]


def headers(grid: TableGrid) -> list[str]:
    rows = expand_rows(grid.header_rows)
    return (
        [" ".join(dict.fromkeys(row[c].text or "" for row in rows)).strip() for c in range(len(rows[0]))]
        if rows
        else []
    )


def unit_from(header: str, default: list[str]) -> str | None:
    for unit in re.findall(r"\(([^)]+)\)", header):
        if unit_class(unit) != "unknown":
            return str(unit)
    return default[0] if len(default) == 1 else None


def conditions(text: str) -> dict[str, Any]:
    out: dict[str, Any] = {"text_raw": text}
    text = strip_latex(re.sub(r"\$([^$]*)\$", r"\1", text))
    conc = re.findall(r"\d+(?:\.\d+)?\s*[μµunm]?M\b", text)
    if conc:
        out["compound_conc"] = conc[0]
    times = re.findall(r"\d+(?:\.\d+)?\s*(?:h|min)\b", text)
    coordinated = re.search(r"(\d+(?:\.\d+)?)\s+and\s+(\d+(?:\.\d+)?)\s*(h|min)\b", text)
    if coordinated:
        times = [f"{coordinated[1]} {coordinated[3]}", f"{coordinated[2]} {coordinated[3]}"]
    if times:
        out["time"] = times
    if "fluorescence" in text.lower():
        out["method"] = "fluorescence"
    elif "nephelometry" in text.lower():
        out["method"] = "nephelometry"
    elif "pampa" in text.lower():
        out["method"] = "PAMPA"
    if "sem" in text.lower():
        out["error_type"] = "SEM"
    elif "sd" in text.lower():
        out["error_type"] = "SD"
    return out


def map_measurements(
    grid: TableGrid,
    document_id: str,
    node_ref: str,
    dictionary: MetricDictionary | None = None,
    overrides: dict[str, Any] | None = None,
) -> tuple[list[Measurement], list[Assay], list[dict[str, Any]]]:
    dictionary = dictionary or MetricDictionary()
    overrides = overrides or {}
    names = headers(grid)
    rows = expand_rows(grid.rows)
    measurements = []
    assays = []
    unknown = []
    for r, row in enumerate(rows):
        label = (
            (row[0].text if row else None)
            if grid.orientation == "compounds_as_rows"
            else grid.subject_compound_label
        )
        for c, cell in enumerate(row):
            if c == 0:
                continue
            header = (
                (row[0].text or "")
                if grid.orientation == "properties_as_rows"
                else names[c]
                if c < len(names)
                else f"column_{c}"
            )
            markers = (
                row[0].markers
                if grid.orientation == "properties_as_rows"
                else [
                    m
                    for hrow in grid.header_rows
                    for hcell in hrow
                    if hcell.text and hcell.text in header
                    for m in hcell.markers
                ]
            ) + cell.markers
            footnote = " ".join(grid.footnote_markers.get(marker, "") for marker in markers)
            metric = dictionary.match(header, footnote)
            override = overrides.get(header)
            if override and override.get("metric_id") in dictionary.by_id:
                metric = {**dictionary.by_id[override["metric_id"]], "normalization_warning": "llm_mapping"}
            if metric["id"].startswith("unmapped:"):
                unknown.append({"header": header, "column_index": c, "footnote": footnote})
                # Variables remain SAR assignments, not fake measurements.
                if grid.orientation == "compounds_as_rows":
                    continue
            raw = cell.text or ""
            parsed = parse(raw)
            unit = parsed.unit or unit_from(header, metric.get("default_units", []))
            evidence: list[dict[str, Any]] = [
                {"node_id": node_ref, "role": "primary", "field_path": f"rows[{r}][{c}]"}
            ]
            if cell.bbox:
                evidence[0]["bbox"] = cell.bbox.model_dump()
            aid = new_id("assay")
            cond = conditions(footnote)
            if grid.orientation == "properties_as_rows" and "respectively" in footnote.lower():
                group = [i for i, cells in enumerate(rows) if cells and set(cells[0].markers) & set(markers)]
                times = cond.get("time", [])
                if len(group) == len(times) and r in group:
                    cond["time"] = [times[group.index(r)]]
                    cond["time_assignment"] = "footnote_respectively"
            assays.append(
                Assay(
                    assay_id=aid,
                    document_id=document_id,
                    name_raw=header,
                    assay_type=metric["id"],
                    conditions=cond,
                    evidence=evidence,
                )
            )
            entries: list[tuple[float | None, str | None, int | None]] = [(parsed.value, unit, None)]
            if parsed.parts:
                entries = [(value, unit, index) for index, value in enumerate(parsed.parts)]
            for value, actual_unit, part in entries:
                value_si = None
                try:
                    if value is not None and actual_unit:
                        value_si = to_si(value, actual_unit)
                except (PintError, ValueError, TypeError):
                    pass
                status = (
                    parsed.status
                    if parsed.status in {"explicit", "inactive", "not_determined"}
                    else "needs_review"
                )
                measurements.append(
                    Measurement(
                        measurement_id=new_id("meas"),
                        document_id=document_id,
                        subject_label=label,
                        assay_id=aid,
                        metric=metric["id"],
                        metric_label_raw=header,
                        value_raw=raw,
                        qualifier=parsed.qualifier,
                        value=value,
                        value_upper=parsed.value_upper,
                        error=parsed.error,
                        error_type=cond.get("error_type"),
                        unit_raw=actual_unit,
                        unit=actual_unit,
                        value_si=value_si,
                        status=status,
                        evidence=[{**evidence[0], **({"condition_index": part} if part is not None else {})}],
                        normalization_warning=metric.get("normalization_warning"),
                    )
                )
            for second in parsed.secondary:
                secondary_unit = str(second["unit"]) if second["unit"] else None
                value = float(str(second["value"]))
                measurements.append(
                    Measurement(
                        measurement_id=new_id("meas"),
                        document_id=document_id,
                        subject_label=label,
                        assay_id=aid,
                        metric=str(second["metric"]),
                        metric_label_raw=header,
                        value_raw=raw,
                        value=value,
                        unit=secondary_unit,
                        unit_raw=secondary_unit,
                        value_si=to_si(value, secondary_unit) if secondary_unit else None,
                        status="explicit",
                        evidence=evidence,
                    )
                )
    return measurements, assays, list({item["header"]: item for item in unknown}.values())


def sar_rows(grid: TableGrid, dictionary: MetricDictionary | None = None) -> list[dict[str, Any]]:
    dictionary = dictionary or MetricDictionary()
    names = headers(grid)
    variable_columns = [
        i for i, name in enumerate(names) if i and dictionary.match(name)["id"].startswith("unmapped:")
    ]
    return [
        {
            "label": row[0].text or "",
            "values": {
                names[c]: row[c].mol_id if row[c].mol_id is not None else row[c].text or ""
                for c in variable_columns
                if c < len(row)
            },
        }
        for row in expand_rows(grid.rows)
        if row
    ]
