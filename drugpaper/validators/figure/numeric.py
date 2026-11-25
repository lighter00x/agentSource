from __future__ import annotations

import re
from collections import defaultdict
from typing import Any

from drugpaper.facts.metric_dictionary import MetricDictionary
from drugpaper.facts.units import unit_class
from drugpaper.facts.value_parser import normalized, parse
from drugpaper.validators.figure.codes import CheckResult, issue


def text_match(raw: str, tokens: list[str]) -> bool:
    # Numeric comparison is token bounded: 50 must not match 150 or 50e-6.
    value = normalized(raw).strip()
    value = re.sub(r"\s+", "", value)
    if not value:
        return False
    return any(
        re.search(r"(?<![\d.])" + re.escape(value) + r"(?![\d.eE])", re.sub(r"\s+", "", normalized(token)))
        for token in tokens
    )


def check(ctx: dict[str, Any]) -> list[CheckResult]:
    out = []
    misses = []
    columns = defaultdict(list)
    dictionary = MetricDictionary()
    for i, m in enumerate(ctx.get("measurements", [])):
        path = m.get("evidence", [{}])[0].get("field_path", f"measurements[{i}]")
        p = parse(m.get("value_raw"))
        if p.status == "unparseable":
            out.append(issue("C_VALUE_PARSE", path))
        if (
            ctx.get("text_tokens")
            and p.value is not None
            and not text_match(m.get("value_raw", ""), ctx["text_tokens"])
        ):
            misses.append(path)
        spec = dictionary.by_id.get(m.get("metric"), {})
        if m.get("unit") and spec.get("unit_class") not in {None, "unknown", unit_class(m["unit"])}:
            out.append(issue("C_UNIT_CLASS", path))
        actual = m.get("value_si") if m.get("value_si") is not None else m.get("value")
        if actual is not None:
            for kind in ("hard", "soft"):
                lo, hi = spec.get(kind + "_range", [None, None])
                if lo is not None and actual < lo or hi is not None and actual > hi:
                    out.append(issue("C_RANGE_" + kind.upper(), path))
            if actual > 0:
                columns[m.get("metric")].append((actual, path))
    if misses and len(misses) / max(len(ctx.get("measurements", [])), 1) > 0.1:
        out.extend(issue("C_TEXT_LAYER_MISS", p) for p in misses)
    for values in columns.values():
        if len(values) >= 3:
            ordered = sorted(v for v, _ in values)
            median = ordered[len(ordered) // 2]
            out.extend(issue("C_MAGNITUDE", p) for v, p in values if v / median > 1000 or v / median < 0.001)
    expected = ctx.get("expected_rows")
    rows = ctx.get("extraction", {}).get("rows", [])
    if expected is not None and len(rows) != expected:
        out.append(issue("C_ROW_COUNT", "rows"))
    return out
