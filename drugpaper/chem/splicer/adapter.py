"""稳定的 Markush Tool 契约；保留 prime、限制展开、统一为列表与错误码。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from drugpaper.chem.splicer import vendor


@dataclass
class AssemblyResult:
    status: str
    products: list[str] = field(default_factory=list)
    error_code: str | None = None
    message: str | None = None


def substitute(caption: str, definitions: dict[str, Any], *, max_outputs: int = 64) -> AssemblyResult:
    if not 1 <= max_outputs <= 1024:
        return AssemblyResult("failed", error_code="E_OUTPUT_LIMIT", message="max_outputs 必须在 1..1024")
    if any("position" in key.lower() for key in definitions):
        from drugpaper.chem.splicer.positional import biaryl_aldehyde

        return biaryl_aldehyde(caption, definitions)
    from drugpaper.chem.splicer.labeled_ring import labeled_biaryl

    labeled = labeled_biaryl(caption, definitions)
    if labeled is not None:
        return labeled
    primes = str.maketrans({"′": "'", "’": "'", "″": '"'})

    def hydrogen(value: Any) -> Any:
        if isinstance(value, list):
            return [hydrogen(item) for item in value]
        return "[H]" if value == "H" else value

    def prime_labels(value: str) -> str:
        return value.translate(primes).replace("_double_prime", '"').replace("_prime", "'")

    clean = {prime_labels(key): hydrogen(value) for key, value in definitions.items()}
    try:
        raw = vendor.substitute_markush(prime_labels(caption), clean, max_outputs=max_outputs)
        return AssemblyResult("completed", [raw] if isinstance(raw, str) else raw)
    except ValueError as exc:
        message = str(exc)
        code = (
            "E_OUTPUT_LIMIT"
            if "max_outputs" in message
            else "E_UNDEFINED_VARIABLE"
            if "No Markush definition" in message
            else "E_BRIDGE_ATTACHMENT"
            if "attachment" in message
            else "E_INVALID_SCAFFOLD"
            if "Invalid" in message or "parse" in message.lower()
            else "E_NO_VALID_PRODUCT"
        )
        return AssemblyResult("failed", error_code=code, message=message)


def infer_role(esmiles: str) -> str:
    if not esmiles.strip():
        return "unknown"
    translated = vendor.Translator.refactor(esmiles)
    if translated:
        if translated.sru:
            return "sru"
        if translated.markush or translated.groups:
            return "scaffold"
    return "substituent" if "*" in esmiles.split("<sep>", 1)[0] else "unknown"
