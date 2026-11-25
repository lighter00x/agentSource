from __future__ import annotations

from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from drugpaper.domain.geometry import BBox
from drugpaper.facts.compound_instances import CompoundInstance
from drugpaper.facts.compound_series import (
    CompoundSeries,
    MarkushExplanation,
    PositionPolicy,
    numeric_members,
)


class Cell(BaseModel):
    text: str | None = None
    mol_id: int | None = Field(default=None, ge=0, strict=True)
    markers: list[str] = Field(default_factory=list)
    row_span: int = Field(default=1, ge=1)
    col_span: int = Field(default=1, ge=1)
    bbox: BBox | None = None
    status: Literal["explicit", "unclear", "missing"] = "explicit"
    source: str = "vlm"


class TableGrid(BaseModel):
    compound_instances: list[CompoundInstance] = Field(default_factory=list)
    molecules: list[dict[str, Any]] = Field(default_factory=list)
    molecule_inventory: list[dict[str, Any]] = Field(default_factory=list)
    title: str | None = None
    orientation: Literal["compounds_as_rows", "properties_as_rows", "matrix", "unknown"] = "unknown"
    header_rows: list[list[Cell]] = Field(default_factory=list)
    rows: list[list[Cell]] = Field(default_factory=list)
    footnote_markers: dict[str, str] = Field(default_factory=dict)
    embedded_structure_cells: list[dict[str, int]] = Field(default_factory=list)
    subject_compound_label: str | None = None
    scaffold_mol_id: int | None = Field(default=None, ge=0, strict=True)
    notes: list[str] = Field(default_factory=list)
    status: Literal["ok", "unclear", "not_a_table"] = "ok"


class Assay(BaseModel):
    assay_id: str
    document_id: str
    name_raw: str
    assay_type: str | None = None
    target: str | None = None
    species: str | None = None
    system: str | None = None
    conditions: dict[str, Any] = Field(default_factory=dict)
    evidence: list[dict[str, Any]] = Field(min_length=1)


class Measurement(BaseModel):
    measurement_id: str
    document_id: str
    compound_id: str | None = None
    subject_label: str | None = None
    scaffold_id: str | None = None
    assay_id: str | None = None
    metric: str
    metric_label_raw: str
    value_raw: str
    qualifier: str | None = None
    value: float | None = None
    value_upper: float | None = None
    error: float | None = None
    error_type: str | None = None
    unit_raw: str | None = None
    unit: str | None = None
    value_si: float | None = None
    status: Literal["explicit", "estimated", "not_determined", "inactive", "derived", "needs_review"] = (
        "explicit"
    )
    source_kind: Literal["table", "text", "chart", "figure"] = "table"
    evidence: list[dict[str, Any]] = Field(min_length=1)
    produced_by_artifact_id: str | None = None
    locked: bool = False
    normalization_warning: str | None = None


class Tick(BaseModel):
    pixel: float
    value: float


class Axis(BaseModel):
    title: str = ""
    unit: str | None = None
    scale: Literal["linear", "log10", "category"] = "linear"
    ticks: list[Tick] = Field(default_factory=list)
    categories: list[str] = Field(default_factory=list)


class Series(BaseModel):
    label: str
    color: list[int] | None = None
    points_px: list[list[float]] = Field(default_factory=list)
    points: list[list[float]] = Field(default_factory=list)
    mol_id: int | None = Field(default=None, ge=0, strict=True)
    reference: str | None = None
    error_bars: list[dict[str, float]] = Field(default_factory=list)


class ChartDataset(BaseModel):
    molecules: list[dict[str, Any]] = Field(default_factory=list)
    molecule_inventory: list[dict[str, Any]] = Field(default_factory=list)
    chart_type: Literal["bar", "line", "scatter"]
    x_axis: Axis
    y_axis: Axis
    plot_area: BBox
    series: list[Series]
    status: Literal["ok", "unclear"] = "ok"
    calibration: dict[str, Any] = Field(default_factory=dict)
    overlap_iou: float | None = None


class MoleculeReference(BaseModel):
    markush: MarkushExplanation | None = None
    compound_series: CompoundSeries | None = None
    scaffold_mol_id: int | None = Field(default=None, ge=0, strict=True)
    variants: list[CompoundInstance] = Field(default_factory=list)
    reference: str | None = None
    common_name: str | None = None
    mol_id: int | None = Field(default=None, ge=0, strict=True)
    substituents: dict[str, str | int] = Field(default_factory=dict)
    position_policy: PositionPolicy | None = None
    evidence_text: str = ""
    status: str = "explicit"

    @model_validator(mode="after")
    def series_is_not_an_individual(self) -> Self:
        if self.markush is not None and (self.mol_id is not None or self.substituents):
            raise ValueError("Markush explanations belong to a scaffold, not a concrete mol_id or assignment")
        labels = numeric_members(self.reference or "")
        if labels and len(labels) > 1 and self.compound_series is None:
            raise ValueError(
                "numeric compound ranges require compound_series, not one molecule; keep the printed reference, add compound_series with its members/count, and retain scaffold_mol_id"
            )
        if self.compound_series is not None:
            series = self.compound_series
            if series.variable_definitions and self.markush is None:
                raise ValueError(
                    "a generic series with printed variables requires an endpoint Markush explanation; keep reference and compound_series, add markush with description and variable_options; reactants need not expand cases"
                )
            if not self.reference or self.mol_id is not None or self.substituents:
                raise ValueError(
                    "a series needs its printed reference and optional scaffold_mol_id only as molecule identifiers: reference must be nonblank, mol_id must be null, substituents must be empty; KEEP compound_series and any required markush explanation"
                )
            if labels and set(labels) != set(series.members):
                raise ValueError("series members must match the printed numeric range")
            assigned = {variant.reference for variant in self.variants}
            if not assigned.issubset(series.members):
                raise ValueError("variant labels must belong to their compound series")
            if len(assigned) != len(self.variants):
                raise ValueError("duplicate assignments for a series member")
            if series.structure_status == "unresolved" and self.variants:
                raise ValueError("unresolved series cannot contain instantiated variants")
            if series.structure_status == "resolved" and assigned != set(series.members):
                raise ValueError("resolved series requires explicit assignments for every member")
        return self


class ReactionStep(MoleculeReference):
    model_config = ConfigDict(extra="allow")
    condition: str | dict[str, Any] | None = None
    extra: list[MoleculeReference] = Field(default_factory=list)


class VariantLink(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source: str = Field(min_length=1)
    target: str = Field(min_length=1)
    evidence_text: str = Field(min_length=1)


class ReactionTransformation(BaseModel):
    relationship_type: Literal["reaction", "optimization", "selection", "unresolved"] = "reaction"
    evidence_text: str = ""
    model_config = ConfigDict(extra="allow")
    reactants: list[MoleculeReference] = Field(default_factory=list)
    products: list[MoleculeReference] = Field(default_factory=list)
    conditions: str | dict[str, Any] | None = None
    variant_links: list[VariantLink] = Field(default_factory=list)

    @model_validator(mode="after")
    def links_are_local_to_arrow(self) -> Self:
        for product in self.products:
            if (
                product.compound_series is not None
                and product.compound_series.structure_status == "partial"
                and not product.variants
            ):
                raise ValueError(
                    "partial product series requires explicit variants using current figure structures; otherwise mark unresolved"
                )

        def labels(entities: list[MoleculeReference]) -> set[str]:
            return {
                label
                for e in entities
                for label in ([v.reference for v in e.variants] if e.variants else [e.reference])
                if label
            }

        sources, targets = labels(self.reactants), labels(self.products)
        for link in self.variant_links:
            if link.source not in sources or link.target not in targets:
                raise ValueError("variant_link endpoints must belong to this arrow's reactants/products")
        return self


class ReactionPathway(BaseModel):
    model_config = ConfigDict(extra="allow")
    base_info: MoleculeReference | None = None
    steps: list[ReactionTransformation] = Field(default_factory=list)
    optimization_steps: list[ReactionStep] = Field(default_factory=list)


class ExtractionGap(BaseModel):
    """Skill-reported evidence needs, independent of paper labels and chart type."""

    model_config = ConfigDict(extra="forbid")
    gap_id: str = Field(min_length=1, max_length=74, description="Stable ID; host adds the skill: namespace")
    question: str = Field(min_length=1, max_length=2000)
    field_paths: list[str] = Field(min_length=1, max_length=20)
    category: Literal["missing_evidence", "conflicting_evidence", "unsupported_representation"]
    next_action: Literal["search_text", "inspect_visual", "review_structure", "manual_review"]
    query_terms: list[str] = Field(default_factory=list, max_length=12)
    source_hints: list[str] = Field(default_factory=list, max_length=12)
    evidence_ids: list[str] = Field(default_factory=list, max_length=40)

    @field_validator("field_paths")
    @classmethod
    def concrete_paths(cls, paths: list[str]) -> list[str]:
        if any(not p.startswith("/") or p == "/" or "*" in p for p in paths):
            raise ValueError("Information gaps require non-root concrete JSON pointers")
        return list(dict.fromkeys(paths))


class VisualExtraction(BaseModel):
    compound_instances: list[CompoundInstance] = Field(default_factory=list)
    model_config = ConfigDict(extra="allow")
    molecule_inventory: list[dict[str, Any]] = Field(default_factory=list)
    status: Literal["ok", "unclear"] = "ok"
    evidence_text: str = ""
    molecules: list[MoleculeReference] = Field(default_factory=list)
    base_info: MoleculeReference | None = None
    optimization_steps: list[ReactionStep] = Field(default_factory=list)
    pathways: list[ReactionPathway] = Field(default_factory=list)
    key_findings: list[str] = Field(default_factory=list)
    description: str = ""
    information_gaps: list[ExtractionGap] = Field(default_factory=list, max_length=24)

    @model_validator(mode="after")
    def unresolved_series_needs_review(self) -> Self:
        def unresolved(value: Any) -> bool:
            if isinstance(value, list):
                return any(unresolved(child) for child in value)
            if isinstance(value, dict):
                series = value.get("compound_series")
                if series and series["structure_status"] != "resolved":
                    return True
                return any(unresolved(child) for child in value.values())
            return False

        if len({g.gap_id for g in self.information_gaps}) != len(self.information_gaps):
            raise ValueError("Information gap IDs must be unique within the figure")
        if self.status == "ok" and self.information_gaps:
            raise ValueError("Outstanding information gaps require status=unclear")
        if self.status == "ok" and unresolved(self.model_dump()):
            raise ValueError("unresolved compound-series structures require status=unclear")
        return self


class ReviewItem(BaseModel):
    dimension: Literal["structure", "numeric", "chart", "content"]
    field_path: str
    verdict: Literal["correct", "incorrect", "unsure"]
    suggested_value: Any = None
    reason: str = ""


class ReviewResult(BaseModel):
    items: list[ReviewItem] = Field(default_factory=list)
