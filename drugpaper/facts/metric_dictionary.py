from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml


def key(text: str) -> str:
    text = re.sub(r"\([^)]*\)", "", text)
    return re.sub(r"[^a-z0-9%]", "", text.lower())


def distance(a: str, b: str) -> int:
    row = list(range(len(b) + 1))
    for i, x in enumerate(a, 1):
        nxt = [i]
        for j, y in enumerate(b, 1):
            nxt.append(min(nxt[-1] + 1, row[j] + 1, row[j - 1] + (x != y)))
        row = nxt
    return row[-1]


class MetricDictionary:
    def __init__(self, path: Path = Path("config/metric_dictionary.yaml")):
        self.metrics: list[dict[str, Any]] = yaml.safe_load(path.read_text())["metrics"]
        self.by_id = {m["id"]: m for m in self.metrics}

    def match(self, header: str, footnote: str = "") -> dict[str, Any]:
        normalized = key(header)
        fuzzy = []
        for metric in self.metrics:
            for synonym in metric["synonyms"]:
                base, _, flag = synonym.partition(":")
                if "|footnote~" in base:
                    title, word = base.split("|footnote~")
                    if key(title) == normalized and word.lower() in footnote.lower():
                        return {**metric, "normalization_warning": None}
                    continue
                if key(base) == normalized:
                    return {**metric, "normalization_warning": flag or None}
                if len(normalized) >= 4 and len(key(base)) >= 4:
                    fuzzy.append((distance(normalized, key(base)), metric))
        if fuzzy:
            d, metric = min(fuzzy, key=lambda item: item[0])
            if d <= 2:
                return {**metric, "normalization_warning": f"fuzzy:{header}"}
        return {
            "id": "unmapped:" + header,
            "unit_class": None,
            "default_units": [],
            "normalization_warning": "unmapped",
        }
