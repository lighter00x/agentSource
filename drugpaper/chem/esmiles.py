"""E-SMILES 原文和派生值分离；第一个 separator 后的内容永不改写。"""

from __future__ import annotations

import re
from typing import Any

from drugpaper.chem.splicer.vendor import Translator


def split(e_smiles: str) -> tuple[str, str]:
    core, separator, extension = e_smiles.partition("<sep>")
    return core, extension if separator else ""


def parse(e_smiles: str) -> dict[str, Any]:
    core, extension = split(e_smiles)
    groups = [
        {"type": m[0], "value": m[1]} for m in re.findall(r"<([arcd])>(.*?)</\1>", extension, flags=re.S)
    ]
    translated = Translator.refactor(e_smiles) if "<sep>" in e_smiles else None
    return {
        "core": core,
        "extension": extension,
        "groups": groups,
        "markush": bool(translated and translated.markush),
        "sru": bool(translated and translated.sru),
    }


def assert_roundtrip(raw: str, stored: str) -> None:
    if raw.encode("utf-8") != stored.encode("utf-8"):
        raise ValueError("V-C02: E-SMILES 原文被修改")
