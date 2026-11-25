"""Evidence-bearing deltas and deterministic, pointer-preserving notebook compaction."""

from __future__ import annotations

import json
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from drugpaper.agents.lookup.models import LookupQuestion, LookupResult
from drugpaper.agents.reader.page_pack import PagePack
from drugpaper.capabilities.tokens import estimate_tokens


class Observation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str
    page: int
    evidence_nodes: list[str] = Field(default_factory=list)


class CompoundNote(BaseModel):
    model_config = ConfigDict(extra="forbid")
    label: str
    role_hint: Literal["hit", "lead", "final", "analog", "reference", "unknown"] = "unknown"
    first_page: int
    key_points: list[str] = Field(default_factory=list)
    evidence_nodes: list[str] = Field(default_factory=list)
    unconfirmed: bool = False


class NarrativeEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")
    from_label: str
    to_label: str
    change: str
    motivation: str = ""
    outcome: str = ""
    page: int
    evidence_nodes: list[str] = Field(default_factory=list)
    unconfirmed: bool = False


class NoteDelta(BaseModel):
    model_config = ConfigDict(extra="forbid")
    page: int
    thesis_updates: list[Observation] = Field(default_factory=list)
    compound_updates: list[CompoundNote] = Field(default_factory=list)
    narrative_events: list[NarrativeEvent] = Field(default_factory=list)
    sar_observations: list[Observation] = Field(default_factory=list)
    problems_of_lead: list[Observation] = Field(default_factory=list)
    open_questions: list[LookupQuestion] = Field(default_factory=list)
    resolved_questions: list[str] = Field(default_factory=list)


class ReaderNotes(BaseModel):
    thesis: list[Observation] = Field(default_factory=list)
    compounds: list[CompoundNote] = Field(default_factory=list)
    timeline: list[NarrativeEvent] = Field(default_factory=list)
    sar: list[Observation] = Field(default_factory=list)
    lead_problems: list[Observation] = Field(default_factory=list)
    open_questions: list[LookupQuestion] = Field(default_factory=list)
    lookup_answers: list[LookupResult] = Field(default_factory=list)
    pointers: dict[str, list[int]] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)
    pages_read: list[int] = Field(default_factory=list)
    model_calls: int = 0
    tool_calls: int = 0
    model_records: list[dict[str, Any]] = Field(default_factory=list)

    def tokens(self) -> int:
        return estimate_tokens(self.model_dump_json())


def merge_delta(
    notes: ReaderNotes, delta: NoteDelta, pack: PagePack, labels: set[str], answers: list[LookupResult]
) -> list[str]:
    if delta.page != pack.page:
        raise ValueError("NoteDelta page does not match PagePack")
    allowed = {item["node_ref"] for unit in pack.units for item in unit.items if item.get("node_ref")}
    allowed.update(e.node_id for a in answers for e in a.evidence)
    warnings = []
    collections = [
        (delta.thesis_updates, notes.thesis),
        (delta.sar_observations, notes.sar),
        (delta.problems_of_lead, notes.lead_problems),
    ]
    for incoming, destination in collections:
        for item in incoming:
            if item.page != pack.page or not item.evidence_nodes or not set(item.evidence_nodes) <= allowed:
                warnings.append("rejected observation without current page/lookup evidence")
                continue
            destination.append(item)
            for ref in item.evidence_nodes:
                notes.pointers.setdefault(ref, []).append(item.page)
    for compound in delta.compound_updates:
        if (
            compound.first_page > pack.page
            or not compound.evidence_nodes
            or not set(compound.evidence_nodes) <= allowed
        ):
            warnings.append("rejected compound note without page/lookup evidence")
            continue
        compound.unconfirmed = compound.label not in labels
        if compound.unconfirmed:
            warnings.append(f"未确认编号：{compound.label}")
        notes.compounds.append(compound)
        for ref in compound.evidence_nodes:
            notes.pointers.setdefault(ref, []).append(pack.page)
    for event in delta.narrative_events:
        if event.page != pack.page or not event.evidence_nodes or not set(event.evidence_nodes) <= allowed:
            warnings.append("rejected narrative event without page/lookup evidence")
            continue
        event.unconfirmed = event.from_label not in labels or event.to_label not in labels
        if event.unconfirmed:
            warnings.append(f"未确认编号：{event.from_label}→{event.to_label}")
        notes.timeline.append(event)
        for ref in event.evidence_nodes:
            notes.pointers.setdefault(ref, []).append(event.page)
    notes.pointers = {ref: sorted(set(pages)) for ref, pages in notes.pointers.items()}
    notes.warnings = list(dict.fromkeys([*notes.warnings, *warnings]))
    return warnings


def compact(notes: ReaderNotes, *, max_tokens: int = 8000) -> ReaderNotes:
    if notes.tokens() <= max_tokens:
        return notes
    result = notes.model_copy(deep=True)
    # Older descriptions fold to short text; every node/page pointer stays in the ledger.
    for c in result.compounds:
        if c.role_hint in {"analog", "reference", "unknown"}:
            c.key_points = [p[:80] for p in c.key_points[:1]]
    for collection in (result.thesis, result.sar, result.lead_problems):
        for observation in collection:
            observation.text = observation.text[:180]
    for event in result.timeline:
        event.change = event.change[:100]
        event.motivation = event.motivation[:100]
        event.outcome = event.outcome[:100]
    for a in result.lookup_answers:
        a.answer = a.answer[:100]
    # Fold duplicate narrative versions; original evidence pointers above remain lossless.
    latest = {}
    for event in result.timeline:
        latest[(event.from_label, event.to_label)] = event
    result.timeline = list(latest.values())
    for name in ("thesis", "sar", "lead_problems", "compounds", "lookup_answers"):
        items = getattr(result, name)
        seen: set[str] = set()
        unique = []
        for item in reversed(items):
            key = json.dumps(item.model_dump(), sort_keys=True, ensure_ascii=False)
            if key not in seen:
                unique.append(item)
                seen.add(key)
        setattr(result, name, list(reversed(unique)))
    if result.tokens() > max_tokens:
        # The durable checkpoints retain details, the reading view keeps most recent narrative.
        for name in ("thesis", "sar", "lead_problems", "compounds", "lookup_answers", "timeline"):
            items = getattr(result, name)
            while items and result.tokens() > max_tokens:
                items.pop(0)
    if result.tokens() > max_tokens:
        raise ValueError("reader pointers exceed budget; cannot discard evidence")
    return result
