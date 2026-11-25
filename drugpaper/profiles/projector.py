"""Project facts to the fixed legacy delivery shape and an evidence-bearing audit twin."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any, cast

import yaml
from pydantic import BaseModel, Field

from drugpaper.domain.nodes import EvidenceRef
from drugpaper.facts.full_text import FullTextContext, valid_evidence
from drugpaper.facts.metric_dictionary import MetricDictionary
from drugpaper.facts.paths import PathProposal, Step

PROFILE_ROOT = Path(__file__).parent / "drug_optimization_v1"


class ProjectionResult(BaseModel):
    delivery: dict[str, Any]
    audit: dict[str, Any]
    warnings: list[str] = Field(default_factory=list)


def export_smiles(compound: dict[str, Any]) -> str:
    structure = compound.get("accepted_structure") or {}
    if structure.get("is_markush"):
        return "null"
    return str(structure.get("e_smiles_raw") or f"compound {compound.get('primary_label', '?')}")


def measurement_text(m: dict[str, Any]) -> str:
    raw, unit = m["value_raw"], m.get("unit_raw") or m.get("unit") or ""
    if unit and unit.casefold().replace("µ", "μ") not in raw.casefold().replace("µ", "μ"):
        return f"{raw} {unit}"
    return str(raw)


def _assay(ctx: FullTextContext, m: dict[str, Any]) -> dict[str, Any]:
    return next((a for a in ctx.assays if a["assay_id"] == m.get("assay_id")), {})


def _conditions(ctx: FullTextContext, m: dict[str, Any]) -> dict[str, Any]:
    a = _assay(ctx, m)
    return {
        "species": a.get("species"),
        "target": a.get("target"),
        "system": a.get("system"),
        **a.get("conditions", {}),
    }


def _condition_key(ctx: FullTextContext, m: dict[str, Any]) -> str:
    c = _conditions(ctx, m)
    # Raw footnote prose may differ across documents, explicit assay conditions remain authoritative.
    return json.dumps(
        {k: v for k, v in c.items() if k not in {"text_raw", "error_type"} and v not in (None, "", [])},
        sort_keys=True,
        ensure_ascii=False,
    )


def _rank(ctx: FullTextContext, m: dict[str, Any]) -> tuple[int, int, int]:
    cond = _conditions(ctx, m)
    species = str(cond.get("species") or cond.get("text_raw", "")).casefold()
    score = (
        0
        if any(x in species for x in ("human", "人"))
        else 1
        if any(x in species for x in ("rat", "大鼠"))
        else 2
        if any(x in species for x in ("mouse", "mice", "小鼠"))
        else 3
    )
    return (
        score,
        0 if m.get("status") == "explicit" else 1,
        0 if any(e.get("role", "primary") == "primary" for e in m["evidence"]) else 1,
    )


def _subject(ctx: FullTextContext, compound: dict[str, Any]) -> list[dict[str, Any]]:
    ids, label = compound.get("compound_id"), compound.get("primary_label")
    return [
        m
        for m in ctx.measurements
        if (m.get("compound_id") == ids if m.get("compound_id") else m.get("subject_label") == label)
        and m.get("status") in {"explicit", "estimated"}
        and not m.get("normalization_warning")
    ]


def project(proposal: PathProposal, steps: list[Step], ctx: FullTextContext) -> ProjectionResult:
    delivery = json.loads((PROFILE_ROOT / "template.json").read_text())
    config = yaml.safe_load((PROFILE_ROOT / "profile.yaml").read_text())
    warnings: list[str] = []
    annotations: dict[str, dict[str, Any]] = {}
    compounds = ctx.by_id()
    lead = compounds.get(proposal.lead_compound_id, {"primary_label": "?"})
    final = compounds.get(proposal.final_compound_id, {"primary_label": "?"})

    def record(
        path: str, value: Any, evidence: list[dict[str, Any]], source_ids: list[str] | None = None
    ) -> Any:
        evidence = [e for e in evidence if valid_evidence(ctx.ir, e)]
        if value not in ("null", "", [], {}) and not evidence:
            warnings.append(f"no evidence; nulled: {path}")
            value = "null"
        annotations[path] = {"value": value or "null", "evidence": evidence, "source_ids": source_ids or []}
        return value or "null"

    def refs(ids: list[str]) -> list[dict[str, Any]]:
        return [EvidenceRef(node_id=ref).model_dump(mode="json", exclude_none=True) for ref in ids]

    def ce(c: dict[str, Any]) -> list[dict[str, Any]]:
        return cast(
            list[dict[str, Any]], (c.get("accepted_structure") or {}).get("evidence") or c.get("evidence", [])
        )

    title_refs = [n.ref for n in ctx.ir.nodes if n.subtype == "title" and n.status != "removed"]
    author_refs = [n.ref for n in ctx.ir.nodes if n.subtype == "authors" and n.status != "removed"]
    delivery["基本信息"] = {
        "论文题目": record("基本信息.论文题目", ctx.ir.meta.title or "null", refs(title_refs)),
        "论文作者": record("基本信息.论文作者", ", ".join(ctx.ir.meta.authors) or "null", refs(author_refs)),
        "论文研究内容以及主要贡献": record(
            "基本信息.论文研究内容以及主要贡献",
            proposal.summary.text or "null",
            refs(proposal.summary.evidence_node_ids),
        ),
    }
    tag_evidence = refs(
        list(
            dict.fromkeys(
                [
                    *proposal.summary.evidence_node_ids,
                    *[ref for s in proposal.strategies for ref in s.evidence_node_ids],
                ]
            )
        )
    )
    delivery["成药性优化方向标签"] = []
    for tag in proposal.tags:
        if tag not in config["tag_categories"]:
            warnings.append(f"新增标签需复核：{tag}")
        value = record(f"成药性优化方向标签[{len(delivery['成药性优化方向标签'])}]", tag, tag_evidence)
        if value != "null":
            delivery["成药性优化方向标签"].append(value)
    lead_smiles = record(
        "先导化合物分子信息.SMILES", export_smiles(lead), ce(lead), [lead.get("compound_id", "")]
    )
    problems = [claim for claim in proposal.lead_problems if claim.text and refs(claim.evidence_node_ids)]
    delivery["先导化合物分子信息"] = {
        "SMILES": lead_smiles,
        "存在问题": record(
            "先导化合物分子信息.存在问题",
            "；".join(c.text for c in problems) or "null",
            refs([r for c in problems for r in c.evidence_node_ids]),
        ),
    }
    for i, step in enumerate(steps, 1):
        p = step.proposed
        a, b = (
            compounds.get(p.from_id, {"primary_label": "?"}),
            compounds.get(p.to_id, {"primary_label": "?"}),
        )
        path = f"steps.{i}"
        evidence = refs(p.evidence_node_ids)
        changes = []
        for j, region in enumerate(step.diff.regions if step.diff else []):
            changes.append(
                {
                    "原始片段": record(
                        f"{path}.smiles_change[{j}].原始片段", region.original_fragment or "null", ce(a)
                    ),
                    "替代片段": record(
                        f"{path}.smiles_change[{j}].替代片段", region.replacement_fragment or "null", ce(b)
                    ),
                    "说明": record(f"{path}.smiles_change[{j}].说明", p.change_text, evidence),
                }
            )
        structure = b.get("accepted_structure") or {}
        delivery["steps"][str(i)] = {
            "start": record(f"{path}.start", export_smiles(a), ce(a), [p.from_id]),
            "change": record(f"{path}.change", p.change_text or "null", evidence),
            "smiles_change": changes,
            "end": {
                "smiles": record(f"{path}.end.smiles", export_smiles(b), ce(b), [p.to_id]),
                "extra": {
                    "scaffold": record(
                        f"{path}.end.extra.scaffold",
                        structure.get("e_smiles_raw") if structure.get("is_markush") else "null",
                        ce(b),
                    ),
                    "substituent": "null",
                },
            },
            "description": record(f"{path}.description", p.rationale or "null", evidence),
            "flag": record(f"{path}.flag", p.flag_label or "null", evidence),
        }
        if step.status == "needs_review":
            warnings.extend(f"step {i}: {w}" for w in step.warnings)
    for s in proposal.strategies:
        i = len(delivery["优化策略"])
        evidence = refs(s.evidence_node_ids)
        delivery["优化策略"].append(
            {
                k: record(f"优化策略[{i}].{k}", value or "null", evidence)
                for k, value in (
                    ("策略类型", s.strategy_type),
                    ("具体操作", s.operation),
                    ("目标改善", s.target_improvement),
                )
            }
        )
    for sar_claim in proposal.sar_rules:
        delivery["关键构效关系"].append(
            record(
                f"关键构效关系[{len(delivery['关键构效关系'])}]",
                sar_claim.text or "null",
                refs(sar_claim.evidence_node_ids),
            )
        )
    comparison = delivery["优化前后关键指标对比"]
    for prefix, c in (("original", lead), ("optimized", final)):
        comparison[f"{prefix}_molecule"] = record(
            f"优化前后关键指标对比.{prefix}_molecule", export_smiles(c), ce(c), [c.get("compound_id", "")]
        )
        name = f"化合物{c['primary_label']}" + (f" ({', '.join(c['aliases'])})" if c.get("aliases") else "")
        comparison[f"{prefix}_molecule_name"] = record(
            f"优化前后关键指标对比.{prefix}_molecule_name", name, c.get("evidence", []) or ce(c)
        )
    dictionary = MetricDictionary()
    original, optimized = _subject(ctx, lead), _subject(ctx, final)
    # Compare the same assay/condition, never join two values solely by a broad metric name.
    pairs = []
    for a in original:
        for b in optimized:
            if a["metric"] == b["metric"] and _condition_key(ctx, a) == _condition_key(ctx, b):
                pairs.append((a, b))
    categories = {category for tag in proposal.tags for category in config["tag_categories"].get(tag, [])}
    pairs.sort(
        key=lambda pair: (
            0 if dictionary.by_id.get(pair[0]["metric"], {}).get("category") in categories else 1,
            _rank(ctx, pair[1]),
            pair[0]["metric"],
        )
    )
    picked: set[str] = set()
    sources: dict[str, list[dict[str, Any]]] = {"original": [], "optimized": []}
    for a, b in pairs:
        metric = a["metric"]
        if metric in picked or len(picked) >= 8:
            continue
        # Conflicting replicates for a condition cannot be silently chosen.
        group = [
            (x, y)
            for x, y in pairs
            if x["metric"] == metric and _condition_key(ctx, x) == _condition_key(ctx, a)
        ]
        if (
            len({measurement_text(x) for x, _ in group}) > 1
            or len({measurement_text(y) for _, y in group}) > 1
        ):
            warnings.append(f"comparison conflict: {metric}")
            continue
        picked.add(metric)
        key = a["metric_label_raw"]
        for prefix, m in (("original", a), ("optimized", b)):
            comparison[f"{prefix}_molecule_properties"][key] = record(
                f"优化前后关键指标对比.{prefix}_molecule_properties.{key}",
                measurement_text(m),
                m["evidence"],
                [m["measurement_id"]],
            )
            sources[prefix].extend(m["evidence"])
    if len(picked) < 3:
        warnings.append(f"only {len(picked)} comparable metrics available; no values invented")
    for prefix in ("original", "optimized"):
        e = sources[prefix]
        comparison[f"{prefix}_molecule_properties"]["来源"] = record(
            f"优化前后关键指标对比.{prefix}_molecule_properties.来源",
            "；".join(dict.fromkeys(x["node_id"] for x in e)) or "null",
            e,
        )
    selected: set[str] = set()
    for export_key, field in config["metric_fields"].items():
        candidates = [
            m for m in optimized if dictionary.by_id.get(m["metric"], {}).get("export_key") == export_key
        ]
        if export_key == "logd":
            candidates = [
                m for m in candidates if "7.4" in str(_conditions(ctx, m)) or "7.4" in m["metric_label_raw"]
            ]
        if export_key in {"bioavailability", "ld50"}:
            candidates = [
                m
                for m in candidates
                if ("human" if export_key == "bioavailability" else "rat") in str(_conditions(ctx, m)).lower()
                and "oral" in str(_conditions(ctx, m)).lower()
            ]
        candidates.sort(key=lambda m: (_rank(ctx, m), m["measurement_id"]))
        if not candidates:
            continue
        rank = _rank(ctx, candidates[0])
        best = [m for m in candidates if _rank(ctx, m) == rank]
        if len({(_condition_key(ctx, m), measurement_text(m)) for m in best}) > 1:
            warnings.append(f"ambiguous primary metric: {field}")
            continue
        m = best[0]
        category, name = field.split(".", 1)
        delivery["分子数据指标"][category][name] = record(
            f"分子数据指标.{field}", measurement_text(m), m["evidence"], [m["measurement_id"]]
        )
        selected.add(m["measurement_id"])

    def audit(value: Any, path: str = "") -> Any:
        if isinstance(value, dict):
            return {k: audit(v, f"{path}.{k}" if path else k) for k, v in value.items()}
        if isinstance(value, list):
            return [audit(v, f"{path}[{i}]") for i, v in enumerate(value)]
        return copy.deepcopy(annotations.get(path, {"value": value, "evidence": [], "source_ids": []}))

    audit_result = audit(delivery)
    audit_result["_audit"] = {
        "profile_version": config["version"],
        "document_id": ctx.ir.document_id,
        "revision": ctx.ir.revision,
        "fingerprint": ctx.fingerprint,
        "warnings": warnings,
        "steps": [s.model_dump(mode="json") for s in steps],
        "proposal": proposal.model_dump(mode="json"),
        "additional_measurements": [m for m in ctx.measurements if m["measurement_id"] not in selected],
        "field_sources": annotations,
    }
    return ProjectionResult(delivery=delivery, audit=audit_result, warnings=list(dict.fromkeys(warnings)))
