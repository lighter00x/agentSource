from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from drugpaper.domain.geometry import BBox


class MoleculeBox(BaseModel):
    local_bbox: BBox
    parent_region_index: int = 0
    confidence: float | None = None
    detector_cls: str | None = None
    reading_index: int
    is_scaffold_candidate: bool = False
    masked_regions: list[BBox] = Field(default_factory=list)


class StructureCandidate(BaseModel):
    structure_id: str
    compound_id: str | None = None
    node_ref: str
    source: Literal["ocsr", "opsin", "markush_assembly", "manual", "llm"]
    e_smiles_raw: str | None = None
    smiles_core: str | None = None
    canonical_smiles: str | None = None
    inchikey: str | None = None
    formula: str | None = None
    is_markush: bool = False
    confidence: float | None = Field(default=None, ge=0, le=1)
    validation: dict[str, Any] = Field(default_factory=dict)
    evidence: list[dict[str, Any]] = Field(min_length=1)


class Compound(BaseModel):
    compound_id: str
    document_id: str
    primary_label: str
    aliases: list[str] = Field(default_factory=list)
    role: str = "unknown"
    accepted_structure_id: str | None = None
    stereo_parent_id: str | None = None
    status: Literal["active", "merged", "rejected"] = "active"
    locked_fields: list[str] = Field(default_factory=list)
    evidence: list[dict[str, Any]] = Field(default_factory=list)
    role_evidence: list[dict[str, Any]] = Field(default_factory=list)
    role_status: Literal["unknown", "needs_review", "confirmed"] = "unknown"


class RGroupAssignment(BaseModel):
    assignment_id: str
    scaffold_id: str
    compound_id: str | None = None
    label: str
    values: dict[str, Any]
    substituent_smiles: dict[str, str] = Field(default_factory=dict)
    assembled_structure_id: str | None = None
    evidence: list[dict[str, Any]] = Field(min_length=1)
