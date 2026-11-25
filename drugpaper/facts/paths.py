from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from drugpaper.chem.change_classifier import classify
from drugpaper.chem.diff import MoleculeDiff, fragment_changes, virtual_intermediates
from drugpaper.facts.full_text import FullTextContext


class ClaimDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str = ""
    evidence_node_ids: list[str] = Field(default_factory=list)


class StrategyDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")
    strategy_type: str
    operation: str
    target_improvement: str
    evidence_node_ids: list[str] = Field(default_factory=list)


class ProposedStep(BaseModel):
    model_config = ConfigDict(extra="forbid")
    from_id: str
    to_id: str
    declared_change_types: list[str] = Field(default_factory=list)
    change_text: str
    rationale: str = ""
    flag_label: str = ""
    evidence_node_ids: list[str] = Field(default_factory=list)


class PathProposal(BaseModel):
    model_config = ConfigDict(extra="forbid")
    lead_compound_id: str
    final_compound_id: str
    main_route: list[str]
    steps: list[ProposedStep] = Field(default_factory=list)
    lead_problems: list[ClaimDraft] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    strategies: list[StrategyDraft] = Field(default_factory=list)
    sar_rules: list[ClaimDraft] = Field(default_factory=list)
    summary: ClaimDraft = Field(default_factory=ClaimDraft)
    excluded_analogs: list[dict[str, Any]] = Field(default_factory=list)


class Step(BaseModel):
    proposed: ProposedStep
    diff: MoleculeDiff | None = None
    computed_change_types: list[str] = Field(default_factory=list)
    virtual_intermediates: list[str] = Field(default_factory=list)
    split_steps: list[VirtualStep] = Field(default_factory=list)
    status: Literal["validated", "needs_review"] = "validated"
    warnings: list[str] = Field(default_factory=list)


class VirtualStep(BaseModel):
    start_smiles: str
    end_smiles: str
    to_is_virtual: bool
    diff: MoleculeDiff
    computed_change_types: list[str]
    evidence_node_ids: list[str]


Step.model_rebuild()


def build_steps(proposal: PathProposal, ctx: FullTextContext) -> list[Step]:
    compounds = ctx.by_id()
    steps = []
    for proposed in proposal.steps:
        step = Step(proposed=proposed)
        a = compounds.get(proposed.from_id, {}).get("accepted_structure")
        b = compounds.get(proposed.to_id, {}).get("accepted_structure")
        if not a or not b or a.get("is_markush") or b.get("is_markush"):
            step.status = "needs_review"
            step.warnings.append("structure_missing_or_markush")
        else:
            try:
                start, end = (
                    a.get("smiles_core") or a["e_smiles_raw"],
                    b.get("smiles_core") or b["e_smiles_raw"],
                )
                step.diff = fragment_changes(start, end)
                step.computed_change_types = classify(start, end, step.diff)
                if step.diff.timed_out:
                    step.status = "needs_review"
                if len(step.diff.regions) > 1:
                    try:
                        step.virtual_intermediates = virtual_intermediates(step.diff)
                        route = [start, *step.virtual_intermediates, end]
                        for i, (left, right) in enumerate(zip(route, route[1:], strict=False)):
                            part = fragment_changes(left, right)
                            if len(part.regions) != 1 or part.timed_out:
                                raise ValueError("virtual split still has multiple regions")
                            step.split_steps.append(
                                VirtualStep(
                                    start_smiles=left,
                                    end_smiles=right,
                                    to_is_virtual=i < len(route) - 2,
                                    diff=part,
                                    computed_change_types=classify(left, right, part),
                                    evidence_node_ids=proposed.evidence_node_ids,
                                )
                            )
                        step.warnings.append("virtual_intermediates_require_review")
                    except (ValueError, RuntimeError):
                        step.split_steps = []
                        step.warnings.append("multi_region_split_unavailable")
                    step.status = "needs_review"
            except (ValueError, RuntimeError) as exc:
                step.status = "needs_review"
                step.warnings.append(str(exc))
        steps.append(step)
    return steps
