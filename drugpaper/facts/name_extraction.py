"""Experimental title parsing and evidence-backed OPSIN candidates, without LLM calls."""

from __future__ import annotations

import asyncio
import hashlib
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from drugpaper.chem.formula import check_hrms, parse_hrms
from drugpaper.chem.normalize import canonical
from drugpaper.chem.opsin import OpsinResult
from drugpaper.domain.ids import new_id
from drugpaper.facts.compounds import StructureCandidate
from drugpaper.facts.text_extraction import NAME
from drugpaper.skills.protocol import ArtifactDraft, FactProposal, SkillContext, SkillQuality, SkillResult
from drugpaper.text.normalize import normalize_detail


class NameExtraction(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: str = "skipped"
    name: str | None = None
    label: str | None = None
    structures: list[StructureCandidate] = Field(default_factory=list)
    evidence: list[dict[str, Any]] = Field(default_factory=list)
    source_text_sha256: str
    warnings: list[str] = Field(default_factory=list)


async def extract(inputs: dict[str, Any], ctx: SkillContext) -> SkillResult:
    if ctx.materials is None:
        raise ValueError("缺少正文材料")
    target = ctx.materials.target
    raw = target.get("text_raw") or ""
    detail = normalize_detail(raw)
    result = NameExtraction(source_text_sha256=hashlib.sha256(raw.encode()).hexdigest())
    facts: list[FactProposal] = []
    if target.get("section_role") == "experimental" and (match := NAME.match(detail.norm)):
        result.name, result.label = match["name"], match["label"]
        result.evidence = [
            {"node_id": ctx.target_ref, "char_span": list(detail.map_span(*match.span())), "role": "primary"}
        ]
        response = OpsinResult.model_validate(
            await ctx.tools.call("chem.name_to_structure", chemical_name=result.name)
        )
        result.status = response.status
        if response.status in {"success", "warning"}:
            assert response.smiles is not None
            normalized = await asyncio.to_thread(canonical, response.smiles)
            reported = parse_hrms(detail.norm)
            validation = {
                "valid": normalized.valid,
                "opsin_status": response.status,
                "opsin_message": response.message,
                "opsin_warnings": response.warnings,
                "error": normalized.error,
                **check_hrms(normalized.formula, reported),
            }
            # HRMS must belong to this title paragraph, never a following compound.
            if reported:
                formula_start = detail.norm.find(reported["formula"], match.end())
                if formula_start >= 0:
                    result.evidence.append(
                        {
                            "node_id": ctx.target_ref,
                            "char_span": list(
                                detail.map_span(formula_start, formula_start + len(reported["formula"]))
                            ),
                            "role": "context",
                        }
                    )
            if response.status == "warning":
                result.warnings.append("OPSIN_WARNING")
            if not normalized.valid:
                result.warnings.append("OPSIN_INVALID_STRUCTURE")
            if validation["formula_match"] is False:
                result.warnings.append("V-C03: HRMS formula mismatch")
            elif validation["formula_match"] is None:
                result.warnings.append("HRMS_NOT_VERIFIED")
            candidate = StructureCandidate(
                structure_id=new_id("str"),
                node_ref=ctx.target_ref,
                source="opsin",
                e_smiles_raw=response.smiles,
                smiles_core=response.smiles,
                canonical_smiles=normalized.canonical_smiles,
                inchikey=normalized.inchikey,
                formula=normalized.formula,
                validation=validation,
                evidence=result.evidence,
            )
            result.structures.append(candidate)
            facts = [
                FactProposal(
                    fact_type="structure_candidate", payload=candidate.model_dump(), evidence=result.evidence
                ),
                FactProposal(
                    fact_type="label_claim",
                    payload={
                        "label": result.label,
                        "node_ref": ctx.target_ref,
                        "structure_id": candidate.structure_id,
                        "evidence": result.evidence,
                    },
                    evidence=result.evidence,
                ),
            ]
        else:
            result.warnings.append(f"OPSIN_{response.status.upper()}: {response.message}")
        if result.warnings:
            result.status = "needs_review"
    return SkillResult(
        artifacts=[ArtifactDraft(artifact_type="name_to_structure", payload=result.model_dump(mode="json"))],
        fact_proposals=facts,
        quality=SkillQuality(status="needs_review" if result.warnings else "ok", warnings=result.warnings),
    )
