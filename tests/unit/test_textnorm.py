from __future__ import annotations

import pytest

from drugpaper.docsrc.textnorm import compact_key, strip_latex


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (r"of the \mathrm { D } _ { 1 }receptor", "of the D1 receptor"),
        (r"\mathbf { D } _ { 1 } \mathbf { R } orthosteric", "D1R orthosteric"),
        (r"{ { \bf D } } _ { 1 } and", "D1 and"),
        (r"4 ^ { \prime }-(S-methyl)", "4′-(S-methyl)"),
        ("plain  text\n here", "plain text here"),
    ],
)
def test_strip_latex_delegates_to_normalize(raw: str, expected: str) -> None:
    assert strip_latex(raw) == expected


def test_compact_key_ignores_space_hyphen_case() -> None:
    assert (
        compact_key("dop-amine  Replacement") == compact_key("dopamine re‑placement") == "dopaminereplacement"
    )
