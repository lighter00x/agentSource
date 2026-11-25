"""Canonicalize display markup returned by a VLM, preserving printed cell contents."""

from __future__ import annotations

import re
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from drugpaper.domain.geometry import BBox
from drugpaper.facts.compound_instances import CompoundInstance
from drugpaper.facts.figure_models import Cell, TableGrid


class CellDetail(BaseModel):
    section: Literal["header", "data"] = "data"
    row: int = Field(ge=0, strict=True)
    column: int = Field(ge=0, strict=True)
    mol_id: int | None = Field(default=None, ge=0, strict=True)
    markers: list[str] = Field(default_factory=list)
    row_span: int = Field(default=1, ge=1)
    col_span: int = Field(default=1, ge=1)
    bbox: BBox | None = None
    status: Literal["explicit", "unclear", "missing"] = "explicit"


class CompactTable(BaseModel):
    """Wire schema: ordinary cells are strings, sparse annotations carry metadata."""

    model_config = ConfigDict(extra="forbid")

    compound_instances: list[CompoundInstance] = Field(default_factory=list)
    molecules: list[dict[str, Any]] = Field(default_factory=list)
    molecule_inventory: list[dict[str, Any]] = Field(default_factory=list)
    headers: list[str]
    rows: list[list[str]]
    header_rows: list[list[str]] = Field(default_factory=list)
    orientation: Literal["compounds_as_rows", "properties_as_rows", "matrix", "unknown"] = "unknown"
    subject_compound_label: str | None = None
    scaffold_mol_id: int | None = Field(default=None, ge=0, strict=True)
    footnote_markers: dict[str, str]
    cell_details: list[dict[str, Any]] = Field(
        description="Sparse annotations only: row/column, optional section, mol_id, markers, spans, bbox, status. Omit ordinary cells.",
    )
    status: Literal["ok", "unclear", "not_a_table"] = "ok"

    @model_validator(mode="after")
    def validate_details(self) -> Self:
        for detail in self.cell_details:
            CellDetail.model_validate(detail)
        return self


def from_transcription(payload: dict[str, Any]) -> TableGrid:
    if "headers" not in payload:
        return TableGrid.model_validate(payload)  # existing registered replay payloads
    wire = CompactTable.model_validate(payload)
    grid = TableGrid(
        compound_instances=wire.compound_instances,
        molecules=wire.molecules,
        molecule_inventory=wire.molecule_inventory,
        orientation=wire.orientation,
        subject_compound_label=wire.subject_compound_label,
        scaffold_mol_id=wire.scaffold_mol_id,
        footnote_markers=wire.footnote_markers,
        status=wire.status,
        header_rows=[
            [Cell(text=t) for t in row]
            for row in (wire.header_rows or ([wire.headers] if wire.headers else []))
        ],
        rows=[[Cell(text=t) for t in row] for row in wire.rows],
    )
    seen = set()
    for raw_detail in wire.cell_details:
        detail = CellDetail.model_validate(raw_detail)
        key = (detail.section, detail.row, detail.column)
        if key in seen:
            raise ValueError("duplicate cell annotation")
        seen.add(key)
        rows = grid.header_rows if detail.section == "header" else grid.rows
        if detail.row >= len(rows) or detail.column >= len(rows[detail.row]):
            raise ValueError("cell annotation outside transcribed grid")
        cell = rows[detail.row][detail.column]
        rows[detail.row][detail.column] = cell.model_copy(
            update=detail.model_dump(exclude={"section", "row", "column"})
        )
    return grid


def plain(text: str) -> str:
    text = re.sub(r"</?(?:sup|sub)>", "", text)
    for macro, value in [(r"\mu", "μ"), (r"\times", "×"), (r"\pm", "±"), (r"\leq", "≤"), (r"\geq", "≥")]:
        text = text.replace(macro, value)
    text = re.sub(r"\\(?:mathrm|text)\{([^{}]*)\}", r"\1", text)
    text = re.sub(r"10\^\{([+-−]?\d+)\}", r"10^\1", text)
    text = text.replace("K_{d}", "Kd").replace("K_d", "Kd").replace("K_{i}", "Ki").replace("K_i", "Ki")
    return text.replace("$", "").strip()


def recover_markdown_grid(grid: TableGrid) -> TableGrid:
    if grid.rows or not grid.title or "|" not in grid.title:
        return grid
    lines = grid.title.splitlines()
    parsed: list[list[Cell]] = []
    separator = None
    last = -1
    for i, line in enumerate(lines):
        if not line.strip().startswith("|"):
            continue
        values = [s.strip() for s in line.strip().strip("|").split("|")]
        if all(re.fullmatch(r":?-{3,}:?", s) for s in values):
            separator = len(parsed)
            continue
        parsed.append([Cell(text=v) for v in values])
        last = i
    if separator is None or len(parsed) <= separator:
        return grid
    grid.header_rows = parsed[:separator]
    grid.rows = parsed[separator:]
    tail = " ".join(lines[last + 1 :])
    markers = list(re.finditer(r"\$?\^\{?([a-z])\}?\$?", tail))
    for index, marker in enumerate(markers):
        end = markers[index + 1].start() if index + 1 < len(markers) else len(tail)
        grid.footnote_markers[marker[1]] = plain(tail[marker.end() : end])
    grid.title = next(
        (line.strip() for line in lines if line.strip() and not line.strip().startswith("|")), None
    )
    grid.notes.append("canonicalized_markdown_transcription")
    return grid


def canonicalize(grid: TableGrid) -> TableGrid:
    grid = recover_markdown_grid(grid)
    for row in grid.header_rows + grid.rows:
        for cell in row:
            if cell.text:
                html = re.findall(r"<sup>([a-z])</sup>", cell.text)
                math = re.findall(r"\$?\^\{?([a-z])\}?\$?", cell.text)
                cell.markers = list(dict.fromkeys(cell.markers + html + math))
                text = re.sub(r"<sup>[a-z]</sup>", "", cell.text)
                text = re.sub(r"\$?\^\{?[a-z]\}?\$?", "", text)
                cell.text = plain(text)
    if grid.orientation == "unknown" and grid.header_rows and grid.header_rows[0]:
        first_header = (grid.header_rows[0][0].text or "").lower().strip().rstrip(".")
        if first_header in {"compd", "cmpd", "compound", "compounds"}:
            grid.orientation = "compounds_as_rows"
    if grid.orientation == "compounds_as_rows":
        grid.subject_compound_label = None
    if not grid.rows and grid.status == "ok":
        grid.status = "unclear"
        grid.notes.append("model_returned_no_structured_rows")
    return grid
