from __future__ import annotations

import json
import re
from typing import Any

from jsonschema import Draft202012Validator

from drugpaper.facts.full_text import FullTextContext, valid_evidence
from drugpaper.profiles.projector import PROFILE_ROOT, ProjectionResult, export_smiles
from drugpaper.validators.figure.codes import CheckResult


def validate_projection(result: ProjectionResult, ctx: FullTextContext) -> list[CheckResult]:
    value = result.delivery
    issues = []

    def fail(code: str, path: str, message: str) -> None:
        issues.append(
            CheckResult(code=code, dimension="projection", severity="error", field_path=path, message=message)
        )

    schema = json.loads((PROFILE_ROOT / "output.schema.json").read_text())
    for err in Draft202012Validator(schema).iter_errors(value):
        fail("V-J01", ".".join(map(str, err.path)), err.message)
    comparison = value.get("优化前后关键指标对比", {})
    steps = value.get("steps", {})
    ordered = sorted(steps, key=lambda s: int(s) if str(s).isdigit() else 999999)
    lead = value.get("先导化合物分子信息", {}).get("SMILES")
    if lead != comparison.get("original_molecule"):
        fail("V-J02", "先导化合物分子信息.SMILES", "先导与对比原始分子不一致")
    if ordered:
        if (
            ordered != [str(i) for i in range(1, len(ordered) + 1)]
            or steps[ordered[0]].get("start") != lead
            or steps[ordered[-1]].get("end", {}).get("smiles") != comparison.get("optimized_molecule")
            or any(
                steps[a].get("end", {}).get("smiles") != steps[b].get("start")
                for a, b in zip(ordered, ordered[1:], strict=False)
            )
        ):
            fail("V-J02", "steps", "步骤编号、首尾或中间结构不连续")
    elif lead != comparison.get("optimized_molecule"):
        fail("V-J02", "steps", "先导与最终产物不同却没有优化步骤")
    audit = result.audit.get("_audit", {})
    proposal = audit.get("proposal", {})
    by_id = ctx.by_id()
    for path, compound_id in (
        ("先导化合物分子信息.SMILES", proposal.get("lead_compound_id")),
        ("优化前后关键指标对比.original_molecule", proposal.get("lead_compound_id")),
        ("优化前后关键指标对比.optimized_molecule", proposal.get("final_compound_id")),
    ):
        stored: Any = value
        for key in path.split("."):
            stored = stored.get(key) if isinstance(stored, dict) else None
        if (
            compound_id in by_id
            and stored not in ("null", None)
            and stored != export_smiles(by_id[compound_id])
        ):
            fail("V-J03", path, "E-SMILES 与存储原文不同")
    for i, p in enumerate(proposal.get("steps", []), 1):
        for field, cid in (("start", p["from_id"]), ("end.smiles", p["to_id"])):
            actual = (
                steps.get(str(i), {}).get("start")
                if field == "start"
                else steps.get(str(i), {}).get("end", {}).get("smiles")
            )
            if cid in by_id and actual not in ("null", None) and actual != export_smiles(by_id[cid]):
                fail("V-J03", f"steps.{i}.{field}", "E-SMILES 与存储原文不同")

    # Audit annotations must match the delivered leaf, otherwise evidence would describe another value.
    def walk(value: Any, annotation: Any, path: str = "") -> None:
        if isinstance(value, dict):
            for key, child in value.items():
                walk(
                    child,
                    annotation.get(key, {}) if isinstance(annotation, dict) else {},
                    f"{path}.{key}" if path else key,
                )
        elif isinstance(value, list):
            for i, child in enumerate(value):
                walk(
                    child,
                    annotation[i] if isinstance(annotation, list) and i < len(annotation) else {},
                    f"{path}[{i}]",
                )
        elif value != "null":
            evidence = annotation.get("evidence", []) if isinstance(annotation, dict) else []
            if (
                annotation.get("value") != value
                or not evidence
                or not any(
                    e.get("role", "primary") == "primary" and valid_evidence(ctx.ir, e) for e in evidence
                )
            ):
                fail("V-E01", path, "非空事实缺少与交付值一致的当前primary证据")

    walk(value, result.audit)
    for prefix in ("original", "optimized"):
        properties = comparison.get(f"{prefix}_molecule_properties", {})
        if not properties or list(properties)[-1] != "来源":
            fail("V-J04", prefix, "来源必须是properties最后一个字段")
        for key, text in properties.items():
            if (
                key != "来源"
                and isinstance(text, str)
                and re.search(r"compound\s*\d|化合物\s*\d|^\s*\d+[a-z]?\s*:", text, re.I)
            ):
                fail("V-J04", f"{prefix}.{key}", "指标value重复含化合物编号")
    prose: list[tuple[str, str]] = [
        (
            "基本信息.论文研究内容以及主要贡献",
            value.get("基本信息", {}).get("论文研究内容以及主要贡献", "null"),
        ),
        ("先导化合物分子信息.存在问题", value.get("先导化合物分子信息", {}).get("存在问题", "null")),
    ]
    for i, step in steps.items():
        prose.extend((f"steps.{i}.{k}", step.get(k, "null")) for k in ("change", "description", "flag"))
        prose.extend(
            (f"steps.{i}.smiles_change.说明", s.get("说明", "null")) for s in step.get("smiles_change", [])
        )
    for i, s in enumerate(value.get("优化策略", [])):
        prose.extend((f"优化策略[{i}].{k}", text) for k, text in s.items())
    prose.extend(("关键构效关系", s) for s in value.get("关键构效关系", []))
    prose.extend(("成药性优化方向标签", s) for s in value.get("成药性优化方向标签", []))
    for path, text in prose:
        if not isinstance(text, str) or text == "null":
            continue
        chinese = len(re.findall(r"[\u4e00-\u9fff]", text))
        latin = len(re.findall(r"[A-Za-z]", text))
        if chinese == 0 or (latin > 30 and chinese / (chinese + latin) < 0.1):
            fail("V-J05", path, "解释字段中文比例不足")
    return issues
