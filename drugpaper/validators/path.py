"""V-P01..08; evidence existence never substitutes for a direct optimization link."""

from __future__ import annotations

import re
from typing import Any

from drugpaper.chem.diff import fragment, molecule, replace_regions
from drugpaper.chem.normalize import canonical
from drugpaper.chem.ring_lexicon import validate_ring_names
from drugpaper.facts.full_text import FullTextContext
from drugpaper.facts.paths import PathProposal, Step
from drugpaper.validators.figure.codes import CheckResult

CHANGE_ALIASES = {
    "环替换": "ring_replacement",
    "N-去甲基化": "n_dealkylation",
    "去甲基化": "n_dealkylation",
    "去烷基化": "n_dealkylation",
    "N-去烷基化": "n_dealkylation",
    "卤代": "halogenation",
    "甲基化": "methylation",
    "羟基化": "hydroxylation",
    "环化": "cyclization",
    "开环": "ring_opening",
    "链长变化": "chain_length_change",
    "立体变化": "stereochemistry",
}
RELATION = r"optimi[sz]|convert|replac|demethyl|dealkyl|modif|afford|yield|led\s+to|derive|obtain|improv|优化|得到|转化|替换|去甲基|去烷基|改造|改善|生成|→|->"


def _label(text: str, label: str) -> bool:
    return bool(re.search(r"(?<![A-Za-z0-9])" + re.escape(label) + r"(?![A-Za-z0-9])", text))


def direct_link(ctx: FullTextContext, from_label: str, to_label: str, refs: list[str]) -> bool:
    nodes = {n.ref: n for n in ctx.ir.nodes if n.status != "removed"}
    for ref in refs:
        node = nodes.get(ref)
        if node is None:
            continue
        # Both endpoints and an actual relationship must be in the same sentence.
        for sentence in re.split(r"(?<=[.!?。！？])\s+|[。！？]", node.text_raw or ""):
            if (
                _label(sentence, from_label)
                and _label(sentence, to_label)
                and re.search(RELATION, sentence, re.I)
            ):
                first = re.search(r"(?<![A-Za-z0-9])" + re.escape(from_label) + r"(?![A-Za-z0-9])", sentence)
                last = re.search(r"(?<![A-Za-z0-9])" + re.escape(to_label) + r"(?![A-Za-z0-9])", sentence)
                if (
                    first
                    and last
                    and (
                        first.start() < last.start()
                        or re.search(r"\bfrom\b|由|自", sentence[last.end() : first.start()], re.I)
                    )
                ):
                    return True
        report = ctx.reports.get(ref, {})
        if report.get("status") not in {"ok", "remediated"}:
            continue
        extraction = report.get("extraction", {})

        def arrows(value: Any) -> bool:
            if isinstance(value, list):
                return any(arrows(v) for v in value)
            if not isinstance(value, dict):
                return False
            if "reactants" in value and "products" in value:

                def labels(entities: list[dict[str, Any]]) -> set[str]:
                    return {
                        label
                        for e in entities
                        for label in (
                            [v.get("reference") for v in e.get("variants", [])]
                            if e.get("variants")
                            else [e.get("reference")]
                        )
                        if label
                    }

                sources, targets = labels(value["reactants"]), labels(value["products"])
                if len(sources) == len(targets) == 1 and from_label in sources and to_label in targets:
                    return True
                if any(
                    link.get("source") == from_label
                    and link.get("target") == to_label
                    and link.get("evidence_text")
                    for link in value.get("variant_links", [])
                ):
                    return True
            return any(
                arrows(v) for k, v in value.items() if k not in {"rejected_variant_links", "semantic_errors"}
            )

        if arrows(extraction):
            return True
    return False


def validate_path(proposal: PathProposal, steps: list[Step], ctx: FullTextContext) -> list[CheckResult]:
    issues: list[CheckResult] = []
    compounds = ctx.by_id()
    refs = {n.ref for n in ctx.ir.nodes if n.status != "removed"}

    def fail(code: str, message: str, index: int | None = None) -> None:
        issues.append(
            CheckResult(
                code=code,
                dimension="path",
                severity="error",
                field_path=f"steps[{index}]" if index is not None else "main_route",
                message=message,
            )
        )
        if index is not None and index < len(steps):
            steps[index].status = "needs_review"
            steps[index].warnings.append(f"{code}: {message}")

    route = proposal.main_route
    endpoints = (
        [proposal.steps[0].from_id, *[s.to_id for s in proposal.steps]]
        if proposal.steps
        else [proposal.lead_compound_id]
    )
    if (
        not route
        or any(i not in compounds for i in route)
        or len(set(route)) != len(route)
        or route[0] != proposal.lead_compound_id
        or route[-1] != proposal.final_compound_id
        or endpoints != route
        or any(a.to_id != b.from_id for a, b in zip(proposal.steps, proposal.steps[1:], strict=False))
    ):
        fail("V-P01", "主路线编号必须真实存在且从先导串联到最终产物")
    excluded = {c.get("compound_id") or c.get("id") for c in proposal.excluded_analogs}
    for index, step in enumerate(steps):
        p = step.proposed
        left, right = compounds.get(p.from_id, {}), compounds.get(p.to_id, {})
        if (
            p.from_id in excluded
            or p.to_id in excluded
            or not p.evidence_node_ids
            or not set(p.evidence_node_ids) <= refs
            or not direct_link(
                ctx, left.get("primary_label", "?"), right.get("primary_label", "?"), p.evidence_node_ids
            )
        ):
            fail("V-P04", "端点缺少同一句优化关系或同一实际箭头证据，或包含已排除支路", index)
        d = step.diff
        if d is None or d.timed_out:
            fail("V-P02", "完整结构缺失或MCS超时，片段无法核验", index)
            continue
        try:
            a, b = molecule(d.start), molecule(d.end)
            for r in d.regions:
                if (
                    fragment(a, r.original_atoms) != r.original_fragment
                    or fragment(b, r.replacement_atoms) != r.replacement_fragment
                ):
                    raise ValueError("片段与原子索引不一致")
            rebuilt = replace_regions(d)
            if canonical(rebuilt).inchikey != canonical(d.end).inchikey:
                raise ValueError("片段重建结果与end InChIKey不同")
        except (ValueError, RuntimeError) as exc:
            fail("V-P02", str(exc), index)
        if len(d.regions) != 1:
            fail("V-P03", "变化区域不是一个；虚拟拆分候选保存在审计中，需复核", index)
        declared = {CHANGE_ALIASES.get(t, t) for t in p.declared_change_types}
        # Small modification claims cannot hide a simultaneous ring/scaffold change.
        if (
            not declared
            or not declared <= set(step.computed_change_types)
            or ("n_dealkylation" in declared and set(step.computed_change_types) != {"n_dealkylation"})
        ):
            fail("V-P05", f"声明{sorted(declared)}与计算类型{step.computed_change_types}不一致", index)
        for message in validate_ring_names(p.change_text, d.start, d.end):
            fail("V-P06", message, index)
        for c in (left, right):
            accepted = c.get("accepted_structure") or {}
            opsin = [
                s
                for s in ctx.structures
                if s.get("compound_id") == c.get("compound_id") and s.get("source") == "opsin"
            ]
            if any(s.get("inchikey") and s.get("inchikey") != accepted.get("inchikey") for s in opsin):
                fail("V-P08", f"化合物{c.get('primary_label')}采纳结构与OPSIN不一致", index)
    label_ids = {c["primary_label"]: c["compound_id"] for c in ctx.compounds}
    for extraction in ctx.extractions.values():
        for statement in extraction.get("optimization_statements", []):
            a, b = label_ids.get(statement.get("from_label")), label_ids.get(statement.get("to_label"))
            if a is None or b is None:
                continue
            # Only narrative-supported route claims involving the selected route are continuity constraints.
            evidence_refs = [e["node_id"] for e in statement.get("evidence", [])]
            if (
                (a in route or b in route)
                and a not in excluded
                and b not in excluded
                and direct_link(ctx, statement["from_label"], statement["to_label"], evidence_refs)
                and (a not in route or b not in route or route.index(a) >= route.index(b))
            ):
                fail(
                    "V-P07", f"直接优化叙事{statement['from_label']}→{statement['to_label']}未按顺序进入路线"
                )
    if any(i.field_path == "main_route" for i in issues):
        for step in steps:
            step.status = "needs_review"
    return issues
