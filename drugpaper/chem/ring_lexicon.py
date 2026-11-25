from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

from drugpaper.chem.change_classifier import ring_compositions


def validate_ring_names(
    text: str, start: str, end: str, path: Path = Path("config/ring_lexicon.yaml")
) -> list[str]:
    lexicon: list[dict[str, Any]] = yaml.safe_load(path.read_text())["rings"]
    rings = ring_compositions(start) + ring_compositions(end)
    failures = []
    for rule in lexicon:
        matched = [
            name
            for name in rule["names"]
            if re.search(
                re.escape(name)
                if re.search(r"[\u4e00-\u9fff]", name)
                else rf"(?<![A-Za-z]){re.escape(name)}",
                text,
                re.I,
            )
        ]
        if matched and not any(
            sum(r.values()) == rule["size"]
            and {k: v for k, v in r.items() if k != "C"} == rule["heteroatoms"]
            for r in rings
        ):
            failures.append("/".join(matched) + "与实际环杂原子组成不符")
    return failures
