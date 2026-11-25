"""Printed compound sets describe labels, never a combinatorial structure generator."""

from __future__ import annotations

import re
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator


def numeric_members(label: str) -> list[str] | None:
    """Expand unambiguous numeric labels only; bound work and preserve first occurrence."""
    members: list[str] = []
    for part in re.split(r"[,，]", label):
        match = re.fullmatch(r"\s*(\d+)\s*(?:[-–−—]\s*(\d+)\s*)?", part)
        if match is None:
            return None
        first, last = int(match[1]), int(match[2] or match[1])
        if last < first or last - first > 500:
            return None
        members.extend(str(i) for i in range(first, last + 1))
        if len(members) > 1000:
            return None
    return list(dict.fromkeys(members))


class CompoundSeries(BaseModel):
    model_config = ConfigDict(extra="forbid")
    members: list[str] = Field(min_length=1, max_length=1000)
    member_count: int = Field(ge=1, le=1000, strict=True)
    variable_definitions: dict[str, str] = Field(default_factory=dict)
    structure_status: Literal["unresolved", "partial", "resolved"] = Field(
        default="unresolved",
        description="Member assignment completeness: resolved means all printed members have source-backed site/position assignments. Assembly success is tracked separately; do not use complete or wait for SMILES to resolve assignments.",
    )
    unresolved_reason: str = ""
    evidence_text: str = Field(min_length=1)

    @model_validator(mode="after")
    def count_labels_only(self) -> Self:
        if any(not s.strip() for s in self.members) or len(set(self.members)) != len(self.members):
            raise ValueError("compound series members must be distinct nonblank printed labels")
        if self.member_count != len(self.members):
            raise ValueError("member_count must count compound labels, not variable combinations")
        if self.structure_status != "resolved" and not self.unresolved_reason.strip():
            raise ValueError("unresolved member structures require a reason")
        return self


PositionRequirement = Literal["required", "optional", "not_applicable", "unknown", "exhausted"]
PositionStatus = Literal["provided", "not_printed", "unreadable", "missing", "not_applicable"]


class PositionPolicy(BaseModel):
    """Whether this endpoint/series needs per-member locants, and where they come from."""

    model_config = ConfigDict(extra="forbid")
    requirement: PositionRequirement = "unknown"
    scope: Literal["per_member", "endpoint"] = "per_member"
    source: Literal["printed_column", "printed_prefix", "cross_reference", "none"] = "none"
    source_ref: str = ""
    basis: str = ""
    evidence_text: str = ""


class MarkushSubstitutionCase(BaseModel):
    """One local substituent possibility, not an instantiated paper compound."""

    model_config = ConfigDict(extra="forbid")
    expression: str = Field(min_length=1)
    group_text: str = Field(min_length=1)
    assignments: list[dict[str, str]] = Field(min_length=1, max_length=64)
    basis: Literal["printed", "symbolic_substitution"] = "symbolic_substitution"
    evidence_text: str = Field(min_length=1)


class MarkushExplanation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    description: str = Field(min_length=1)
    variable_options: dict[str, list[str]] = Field(min_length=1)
    substitution_cases: list[MarkushSubstitutionCase] = Field(default_factory=list, max_length=128)
    expansion_status: Literal["not_expanded", "complete_for_visible_options", "partial", "unclear"]
    notes: str = Field(min_length=1)
    evidence_text: str = Field(min_length=1)

    @model_validator(mode="after")
    def cases_use_printed_options(self) -> Self:
        for name, options in self.variable_options.items():
            if not name.strip() or not options or any(not option.strip() for option in options):
                raise ValueError("Markush variables require nonblank printed options")
            if len(set(options)) != len(options):
                raise ValueError("Markush options must be distinct")
        seen: set[tuple[str, tuple[tuple[str, str], ...]]] = set()
        for case in self.substitution_cases:
            for assignment in case.assignments:
                if not assignment or any(
                    name not in self.variable_options or value not in self.variable_options[name]
                    for name, value in assignment.items()
                ):
                    raise ValueError("Markush substitution cases must use the declared variable options")
                key = (case.expression, tuple(sorted(assignment.items())))
                if key in seen:
                    raise ValueError("duplicate Markush substitution assignment")
                seen.add(key)
        if self.expansion_status == "not_expanded" and self.substitution_cases:
            raise ValueError("not_expanded Markush must not contain substitution cases")
        if self.expansion_status == "complete_for_visible_options" and not self.substitution_cases:
            raise ValueError("complete Markush expansion requires explicit substitution cases")
        return self
