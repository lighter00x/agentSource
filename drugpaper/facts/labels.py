from __future__ import annotations

import re

LABEL_PATTERN = r"(?:\([RS]\)-\d+[a-z]?|[A-Z]{2,}-\d+|\d+[a-z]?)"


def normalize(label: str) -> str:
    value = label.replace("**", "").translate(str.maketrans("‑−–—", "----")).strip()
    value = re.sub(r"^(?:compounds?|cmpd\.?)\s+", "", value, flags=re.I)
    return value.strip(" ,;:")


def expand_range(label: str, maximum: int = 1000) -> list[str]:
    value = normalize(label)
    match = re.fullmatch(r"(\d+)-(\d+)", value)
    if not match:
        return [value]
    start, end = map(int, match.groups())
    if end < start or end - start + 1 > maximum:
        raise ValueError("编号范围无效或超限")
    return [str(i) for i in range(start, end + 1)]
