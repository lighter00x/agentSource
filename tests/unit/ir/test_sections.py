"""S2.4d：章节、前置信息、阅读顺序、F7、F9。"""

from __future__ import annotations

import pytest

from drugpaper.domain import ids
from drugpaper.parsers.mineru_legacy.adapter.front_matter import doi_from_filename
from drugpaper.parsers.mineru_legacy.adapter.sections import (
    is_section_heading,
    role_of_heading,
    run_in_heading,
)
from drugpaper.text.normalize import normalize_detail
from tests.unit.ir.conftest import needs_real
from tests.unit.ir.real import real_ir


@pytest.mark.parametrize(
    ("text", "role"),
    [
        ("INTRODUCTION", "introduction"),
        ("RESULTS AND DISCUSSION", "results"),
        ("CONCLUSIONS", "conclusion"),
        ("EXPERIMENTAL SECTION", "experimental"),
        ("REFERENCES", "references"),
        ("ACKNOWLEDGMENTS", "acknowledgments"),
        ("ACCESS", None),
    ],
)
def test_role_of_heading(text: str, role: str | None) -> None:
    assert role_of_heading(text) == role


def test_section_heading_and_run_in() -> None:
    assert is_section_heading("RESULTS AND DISCUSSION")
    assert not is_section_heading("Supporting Information")
    assert (
        run_in_heading("Identification of Compound 3. An in-house library") == "Identification of Compound 3."
    )
    assert run_in_heading("Synthesis. Unless stated otherwise") == "Synthesis."
    assert run_in_heading("Clearly, among the newly identified modulators") is None
    assert run_in_heading("In this work, we have developed compound 26. It") is None
    assert run_in_heading("Table 1. Effect of") is None
    assert doi_from_filename("10_1021_acs_jmedchem_2c00949") == "10.1021/acs.jmedchem.2c00949"


@needs_real
def test_section_tree() -> None:
    ir = real_ir()
    root = ir.get(ids.ROOT_ID)
    sections = [ir.get(c) for c in root.children if ir.get(c).node_type == "section"]
    labels = [s.label for s in sections]
    for expected in [
        "ABSTRACT",
        "INTRODUCTION",
        "RESULTS AND DISCUSSION",
        "CONCLUSIONS",
        "EXPERIMENTAL SECTION",
        "REFERENCES",
    ]:
        assert expected in labels
    roles = {s.label: s.section_role for s in sections}
    assert roles["RESULTS AND DISCUSSION"] == "results" and roles["EXPERIMENTAL SECTION"] == "experimental"
    assert ir.get("p014.b011").section_role == "references"
    assert ir.get("p003.g000.c0").section_role == "results"  # 子节点继承
    assert root.children[0] == ids.FRONT_ID


@needs_real
def test_run_in_headings_do_not_open_sections() -> None:
    ir = real_ir()
    h = ir.get("p001.b006.h0")
    assert h.node_type == "heading" and h.subtype == "run_in"
    assert h.text_raw == "Identification of Compound 3."
    assert h.parent_id == ir.get("p001.b006").parent_id
    assert ir.get("p001.b006").text_raw.startswith("Identification of Compound 3.")  # 段落文本不变


@needs_real
def test_front_matter() -> None:
    ir = real_ir()
    meta = ir.meta
    assert meta.title and "UCM-1306" in meta.title
    assert meta.journal == "J. Med. Chem." and meta.year == 2022 and meta.volume == "65"
    assert meta.doi == "10.1021/acs.jmedchem.2c00949"
    assert meta.authors[0] == "Javier García-Cárceles" and len(meta.authors) >= 10
    assert all(not a.startswith("and ") for a in meta.authors)
    front = ir.get(ids.FRONT_ID)
    assert "p000.b000" in front.children and ir.get("p000.b000").subtype == "title"
    assert ir.get("p000.b003").section_role == "front_matter"  # "ACCESS" 不开章节


@needs_real
def test_reading_order_is_page_monotonic() -> None:
    ir = real_ir()
    main = [n for n in ir.nodes if n.node_type not in ("document", "front_matter", "section", "discarded")]
    main.sort(key=lambda n: n.reading_order)
    pages = [
        n.page
        for n in main
        if n.page is not None and n.parent_id and ir.get(n.parent_id).node_type in ("section", "front_matter")
    ]
    assert pages == sorted(pages)
    assert len({n.reading_order for n in ir.nodes}) == len(ir.nodes)


@needs_real
def test_f7_normalization_and_mapping() -> None:
    ab = real_ir().get("p000.b007")
    assert "D1 receptor" in ab.text_norm and "\\mathrm" not in ab.text_norm
    d = normalize_detail(ab.text_raw)
    i = d.norm.index("D1 receptor")
    s, e = d.map_span(i, i + len("D1 receptor"))
    assert ab.text_raw[s:e].startswith("D") and ab.text_raw[s:e].endswith("receptor")


@needs_real
def test_f9_discarded_not_in_tree() -> None:
    ir = real_ir()
    discarded = ir.of_type("discarded")
    assert len(discarded) == 93
    assert all(n.parent_id is None for n in discarded)
    tree_ids: set[str] = set()

    def walk(nid: str) -> None:
        tree_ids.add(nid)
        for c in ir.get(nid).children:
            walk(c)

    walk(ids.ROOT_ID)
    assert not tree_ids & {n.local_id for n in discarded}
    assert tree_ids | {n.local_id for n in discarded} == {n.local_id for n in ir.nodes}  # 没有游离节点
