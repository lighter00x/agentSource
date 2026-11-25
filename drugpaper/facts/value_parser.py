"""Preserve original transcription; parse pharmacy values without model calls."""

from __future__ import annotations

import re

from pydantic import BaseModel, Field


class ParsedValue(BaseModel):
    raw: str
    value: float | None = None
    value_upper: float | None = None
    error: float | None = None
    qualifier: str | None = None
    unit: str | None = None
    status: str = "explicit"
    secondary: list[dict[str, object]] = Field(default_factory=list)
    parts: list[float] = Field(default_factory=list)


def normalized(text: str) -> str:
    text = re.sub(r"</?(?:sup|sub)>", "", text)
    value = text.translate(str.maketrans("−–—﹣", "----")).replace("µ", "μ")
    value = value.translate(str.maketrans("⁰¹²³⁴⁵⁶⁷⁸⁹⁻", "0123456789-"))
    return re.sub(r"(?<=\d)\s*[×x·]\s*10\s*\^?\s*([+-]?\d+)", r"e\1", value)


NUMBER = r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?"


def parse(text: str | None) -> ParsedValue:
    raw = text or ""
    value = normalized(raw).strip()
    out = ParsedValue(raw=raw)
    if re.fullmatch(r"(?i)(?:n\.?d\.?|nt|na|n/a|not determined|[-—]|)", value):
        out.status = "not_determined"
        return out
    if value.lower() == "inactive":
        out.status = "inactive"
        return out
    secondary = re.search(rf"\((K[dDiI]|IC50|EC50)\s*=\s*({NUMBER})\s*([^)]*)\)", value)
    if secondary:
        out.secondary.append(
            {
                "metric": secondary[1].lower(),
                "value": float(secondary[2]),
                "unit": secondary[3].strip() or None,
            }
        )
        value = value[: secondary.start()].strip()
    pair = re.fullmatch(rf"({NUMBER})\s*/\s*({NUMBER})(?:\s*([^\d].*))?", value)
    if pair:
        out.parts = [float(pair[1]), float(pair[2])]
        out.value = out.parts[0]
        out.unit = pair[3]
        return out
    match = re.fullmatch(
        rf"\s*(<=|>=|<|>|≤|≥|~|≈)?\s*({NUMBER})(?:\s*(±|\+/-|\+-|-)\s*({NUMBER}))?\s*([^\d]*)", value
    )
    if not match:
        out.status = "unparseable"
        return out
    out.qualifier = {"≤": "<=", "≥": ">=", "≈": "~"}.get(match[1], match[1]) or "="
    out.value = float(match[2])
    if match[3] == "-":
        out.value_upper = float(match[4])
        if out.value_upper < out.value:
            out.status = "unparseable"
    elif match[3]:
        out.error = float(match[4])
    out.unit = match[5].strip() or None
    return out
