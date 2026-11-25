from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from drugpaper.chem.normalize import canonical


def structure_key(value: Any) -> str | None:
    if not isinstance(value, str) or value in {"", "null"} or value.startswith("compound ") or "*" in value:
        return None
    return canonical(value.split("<sep>", 1)[0]).inchikey


def molecule_fields(value: dict[str, Any]) -> tuple[Any, Any]:
    lead = value.get("先导化合物分子信息", value.get("起始分子信息", {}))
    comparison = value.get("优化前后关键指标对比", {})
    steps = value.get("steps", {})
    ordered = (
        sorted(steps, key=lambda s: int(s) if str(s).isdigit() else 999999) if isinstance(steps, dict) else []
    )
    last = steps[ordered[-1]].get("end") if ordered else None
    final = comparison.get("optimized_molecule") or (
        last.get("smiles") or last.get("SMILES") if isinstance(last, dict) else last
    )
    return lead.get("SMILES") or lead.get("smiles") or comparison.get("original_molecule"), final


def _metrics(value: dict[str, Any]) -> dict[str, str]:
    out = {}

    def walk(value: Any, path: str) -> None:
        if isinstance(value, dict):
            for k, v in value.items():
                if k not in {"flag", "来源"}:
                    walk(v, path + "." + k)
        elif value not in (None, "", "null"):
            out[path.casefold().replace(" ", "")] = str(value)

    walk(value.get("分子数据指标", {}), "metrics")
    return out


def chain_valid(value: dict[str, Any]) -> bool | None:
    steps = value.get("steps", {})
    if not isinstance(steps, dict) or not steps:
        return None
    ordered = sorted(steps, key=lambda k: int(k) if str(k).isdigit() else 999999)
    for left, right in zip(ordered, ordered[1:], strict=False):
        end = steps[left].get("end")
        end = end.get("smiles") or end.get("SMILES") if isinstance(end, dict) else end
        start = steps[right].get("start")
        a, b = structure_key(end), structure_key(start)
        if (a is None or b is None) and end != start or a is not None and b is not None and a != b:
            return False
    return True


def compare(reference: dict[str, Any], prediction: dict[str, Any] | None) -> dict[str, Any]:
    prediction = prediction or {}
    old_lead, old_final = molecule_fields(reference)
    new_lead, new_final = molecule_fields(prediction)
    reference_lead, reference_final = structure_key(old_lead), structure_key(old_final)
    lead, final = structure_key(new_lead), structure_key(new_final)
    ref_tags = set(reference.get("成药性优化方向标签", reference.get("研究目标标签", [])))
    pred_tags = set(prediction.get("成药性优化方向标签", prediction.get("研究目标标签", [])))
    ref_metrics, pred_metrics = _metrics(reference), _metrics(prediction)
    common = set(ref_metrics) & set(pred_metrics)
    return {
        "lead_match": lead == reference_lead if reference_lead else None,
        "final_match": final == reference_final if reference_final else None,
        "reference_lead_key": reference_lead,
        "reference_final_key": reference_final,
        "prediction_lead_key": lead,
        "prediction_final_key": final,
        "tag_jaccard": len(ref_tags & pred_tags) / len(ref_tags | pred_tags)
        if ref_tags | pred_tags
        else None,
        "metric_recall": len(common) / len(ref_metrics) if ref_metrics else None,
        "metric_value_agreement": sum(ref_metrics[k] == pred_metrics[k] for k in common) / len(common)
        if common
        else None,
        "reference_metric_count": len(ref_metrics),
        "prediction_metric_count": len(pred_metrics),
        "reference_steps": len(reference.get("steps", {})),
        "prediction_steps": len(prediction.get("steps", {})),
        "reference_chain_valid": chain_valid(reference),
        "prediction_chain_valid": chain_valid(prediction),
    }


def evaluate(reference_dir: Path, prediction_dir: Path, *, expected_count: int = 10) -> dict[str, Any]:
    rows = []
    for path in sorted(reference_dir.glob("gemini_json_backup_*.json")):
        name = path.stem.removeprefix("gemini_json_backup_")
        candidates = [
            prediction_dir / name / "result.drug_optimization_v1.json",
            prediction_dir / f"{name}.json",
        ]
        prediction_path = next((p for p in candidates if p.is_file()), None)
        reference = json.loads(path.read_text())
        try:
            prediction = json.loads(prediction_path.read_text()) if prediction_path else None
            metrics = compare(reference, prediction)
            rows.append(
                {
                    "paper": name,
                    "status": "compared" if prediction_path else "missing_prediction",
                    "reference": str(path),
                    "prediction": str(prediction_path) if prediction_path else None,
                    **metrics,
                }
            )
        except (ValueError, TypeError, KeyError) as exc:
            rows.append({"paper": name, "status": "invalid_json_or_schema", "error": str(exc)[:300]})

    def average(key: str) -> float | None:
        values = [row[key] for row in rows if row.get(key) is not None]
        return sum(values) / len(values) if values else None

    coverage = sum(row["status"] == "compared" for row in rows)
    return {
        "reference_kind": "unreviewed_legacy_gemini_silver",
        "expected_count": expected_count,
        "reference_count": len(rows),
        "prediction_count": coverage,
        "coverage": coverage / expected_count,
        "complete": len(rows) == expected_count and coverage == expected_count,
        "final_match_rate": average("final_match"),
        "lead_match_rate": average("lead_match"),
        "metric_recall": average("metric_recall"),
        "tag_jaccard": average("tag_jaccard"),
        "rows": rows,
    }


def report_markdown(result: dict[str, Any]) -> str:
    text = [
        "# P6 银标准对比",
        "参考来自旧系统Gemini备份，未经人工金标准审核。缺失预测计入覆盖率，并作为未匹配计入可评分字段；不可解析的参考结构不计入结构分母。",
        f"参考 {result['reference_count']}/{result['expected_count']}；本系统覆盖 {result['prediction_count']}/{result['expected_count']}；完整：{result['complete']}。",
        "| 论文 | 状态 | Final | Lead | 标签Jaccard | 指标召回 | steps（参考/本系统） |",
        "|---|---|---|---|---|---|---|",
    ]
    for row in result["rows"]:
        text.append(
            f"| {row['paper']} | {row['status']} | {row.get('final_match')} | {row.get('lead_match')} | {row.get('tag_jaccard')} | {row.get('metric_recall')} | {row.get('reference_steps')}/{row.get('prediction_steps')} |"
        )
    text.extend(
        [
            "",
            "```json",
            json.dumps({k: v for k, v in result.items() if k != "rows"}, ensure_ascii=False, indent=2),
            "```",
            "",
        ]
    )
    return "\n".join(text)
