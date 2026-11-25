from __future__ import annotations

from typing import Any

from drugpaper.chem.splicer.adapter import AssemblyResult, substitute


def assemble_row(scaffold: str, assignment: dict[str, Any]) -> AssemblyResult:
    if any(isinstance(value, (list, dict)) for value in assignment.values()):
        return AssemblyResult("failed", error_code="ambiguous_row")
    return substitute(scaffold, assignment, max_outputs=1)
