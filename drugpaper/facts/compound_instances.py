"""ID-only visual contract shared by SAR rows, reaction products and structure panels."""

from __future__ import annotations

from typing import Annotated, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from drugpaper.facts.compound_series import PositionStatus, numeric_members

MolId = Annotated[int, Field(strict=True, ge=0)]


class CompoundInstance(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "oneOf": [
                {
                    "required": ["mol_id"],
                    "properties": {
                        "mol_id": {"type": "integer"},
                        "scaffold_mol_id": {"type": "null"},
                        "substituents": {"maxProperties": 0},
                    },
                },
                {
                    "required": ["scaffold_mol_id", "substituents"],
                    "properties": {
                        "mol_id": {"type": "null"},
                        "scaffold_mol_id": {"type": "integer"},
                        "substituents": {"minProperties": 1},
                    },
                },
            ]
        },
    )

    reference: str = Field(min_length=1)
    mol_id: MolId | None = None
    scaffold_mol_id: MolId | None = None
    substituents: dict[str, MolId | str] = Field(default_factory=dict)
    attachments: list[Annotated[str, Field(strict=True, min_length=1)]] | None = Field(
        default=None,
        max_length=64,
        json_schema_extra={"uniqueItems": True},
        description="Keys in substituents that participate in assembly, using real scaffold site labels. Other keys are explanatory. None is for legacy records only; new scaffold instances must supply this list.",
    )
    position_definitions: dict[str, str] = Field(default_factory=dict)
    position_status: PositionStatus | None = Field(
        default=None,
        description="Provide when the endpoint declares a position policy: provided/not_printed/unreadable/missing/not_applicable. None is legacy only.",
    )
    row_index: MolId | None = None
    evidence_text: str = Field(min_length=1)

    @model_validator(mode="after")
    def source_is_explicit(self) -> Self:
        if not self.reference.strip() or not self.evidence_text.strip():
            raise ValueError("reference and visual evidence must be nonblank")
        members = numeric_members(self.reference)
        if members and len(members) > 1:
            raise ValueError("a compound range is a series, not one concrete compound instance")
        if (self.mol_id is None) == (self.scaffold_mol_id is None):
            raise ValueError("exactly one of mol_id and scaffold_mol_id is required")
        if self.mol_id is not None and self.substituents:
            raise ValueError("a directly drawn full molecule cannot also be assembled")
        if self.attachments is not None:
            if len(set(self.attachments)) != len(self.attachments) or any(
                not k.strip() for k in self.attachments
            ):
                raise ValueError("attachments must contain distinct nonblank keys")
            if self.scaffold_mol_id is None and self.attachments:
                raise ValueError("attachments require a scaffold")
            if self.scaffold_mol_id is not None and not self.attachments:
                raise ValueError("a scaffold requires at least one explicit attachment")
            if any(k not in self.substituents for k in self.attachments):
                raise ValueError("every attachment must name an existing substituents key")
        if self.position_definitions and (
            self.scaffold_mol_id is None
            or any(not k.strip() or not v.strip() for k, v in self.position_definitions.items())
        ):
            raise ValueError("positional definitions require nonblank source fields and a scaffold")
        if self.position_status == "provided" and not self.position_definitions:
            raise ValueError("position_status=provided requires nonblank position_definitions")
        if self.position_status in {"not_printed", "not_applicable"} and self.position_definitions:
            raise ValueError("position_status contradicts supplied position_definitions")
        if self.position_status in {"missing", "unreadable"} and self.position_definitions:
            raise ValueError("position_status contradicts supplied position_definitions")
        if self.scaffold_mol_id is not None and not self.substituents:
            raise ValueError("a template requires explicit assignments for this individual variant")
        return self
