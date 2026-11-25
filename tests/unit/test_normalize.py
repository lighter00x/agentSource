from __future__ import annotations

import pytest

from drugpaper.text.normalize import compact_key, normalize, normalize_detail, normalize_text


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (r"\mathrm { D } _ { 1 }", "D1"),
        (r"of the \mathrm { D } _ { 1 }receptor", "of the D1 receptor"),
        (r"\mathbf { D } _ { 1 } \mathbf { R } orthosteric", "D1R orthosteric"),
        ("J = 5 4 . 3 Hz", "J = 54.3 Hz"),
        ("a 1:1 mixture of 2 or 4 equiv", "a 1:1 mixture of 2 or 4 equiv"),
        (r"4 ^ { \prime }-(S-methyl)", "4′-(S-methyl)"),
        (r"5 \mu M", "5 μM"),
        (r"12 \pm 3", "12 ± 3"),
        (r"24 \times 10 ^ { - 6 } cm/s", "24 × 10^-6 cm/s"),
        (r"37 ^ { \circ } C", "37°C"),
        (r"\mathbf { 4 5 - 4 7 }", "45-47"),
        (r"{ { \bf D } } _ { 1 } and", "D1 and"),
        ("65 \\% of", "65 % of"),
        ("dopamine re-placement with", "dopamine replacement with"),
        ("4-methyl", "4-methyl"),
        ("long-term", "long-term"),
        ("compounds 1\u221210 were", "compounds 1-10 were"),
        ("upon activation", "upon activation"),
        ("drawbacks uponactivation of", "drawbacks upon activation of"),
        ("treatment ofParkinson\u2019s disease", "treatment of Parkinson\u2019s disease"),
        ("synthesized compound3 as", "synthesized compound 3 as"),
        ("1\u221210were tested", "1-10 were tested"),
        ("Figure 4\x01using", "Figure 4 using"),
        (r"\bulletSupporting Information", "•Supporting Information"),
        (r"in Human\bf { D } _ { 1 } R Endogenously", "in Human D1R Endogenously"),
        ("Expressed in aNeuroblastoma Cell", "Expressed in a Neuroblastoma Cell"),
        (r"\mathbf { D } _ { 1 } \mathbf { R } allosteric", "D1R allosteric"),
        (r"R _ { \mathrm { f } } \colon 0 . 3 9 (hexane", "Rf :0.39 (hexane"),
        (r"m\mathrm { 1 p ~ 4 6 - 4 7 ~ ^ { \circ } C }", "m1p 46-47 °C"),
        ("mRNA and cAMP", "mRNA and cAMP"),
    ],
)
def test_normalize_rules(raw: str, expected: str) -> None:
    assert normalize_text(raw) == expected


def test_chemical_words_not_split() -> None:
    for w in ["methanesulfonimidoyl", "sulfoximine", "biphenyl", "fluoromethoxy"]:
        assert normalize_text(w) == w


def test_offsets_map_back_to_raw() -> None:
    raw = r"selective modulators of the \mathrm { D } _ { 1 } receptor, the screening"
    d = normalize_detail(raw)
    i = d.norm.index("D1 receptor")
    s, e = d.map_span(i, i + len("D1 receptor"))
    assert raw[s:e].startswith("D") and raw[s:e].endswith("receptor")
    assert r"_ { 1 }" in raw[s:e]
    # 普通文字逐字对应
    j = d.norm.index("screening")
    assert raw[slice(*d.map_span(j, j + 9))] == "screening"


def test_offsets_for_inserted_symbol() -> None:
    raw = r"IC50 = 5 \mu M"
    d = normalize_detail(raw)
    k = d.norm.index("μ")
    assert raw[slice(*d.map_span(k, k + 1))].startswith(r"\mu")


def test_normalize_api_shape() -> None:
    norm, offsets = normalize("  a  b ")
    assert norm == "a b" and len(offsets) == len(norm) + 1 and offsets[-1] == 7
    assert normalize_detail("").map_span(0, 3) == (0, 0)


def test_compact_key() -> None:
    assert compact_key(r"the \mathrm { D } _ { 1 }receptor") == compact_key("the D1 receptor")
