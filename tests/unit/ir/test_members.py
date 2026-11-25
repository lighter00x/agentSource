"""S2.4b：成员处理与图注（F1～F4）。"""

from __future__ import annotations

from drugpaper.parsers.mineru_legacy.adapter.builder import build_ir
from tests.unit.ir.conftest import needs_real
from tests.unit.ir.real import real_ir
from tests.unit.ir.synth import LONG, blk, page


@needs_real
def test_f1_tables_are_image_assets() -> None:
    ir = real_ir()
    tables = {n.label: n for n in ir.of_type("table")}
    assert set(tables) == {"Table 1", "Table 2", "Table 3"}
    for t in tables.values():
        assert t.attributes["image_path"].startswith("merged_images/")
        assert t.text_raw is None  # 无 HTML，内容只有图片


@needs_real
def test_f2_sar_table_merged_as_image_body_is_table() -> None:
    t1 = real_ir().get("p003.g000")
    assert t1.node_type == "table" and t1.label == "Table 1"
    assert t1.source_ref["block_type"] == "image_body"
    s1 = real_ir().get("p002.g001")
    assert s1.node_type == "scheme" and s1.label == "Scheme 1"


@needs_real
def test_f3_caption_from_member_title() -> None:
    ir = real_ir()
    t1 = ir.get("p003.g000")
    captions = [ir.get(c) for c in t1.children if ir.get(c).node_type == "caption"]
    assert [c.local_id for c in captions] == ["p003.g000.c0"]
    assert captions[0].text_norm.startswith("Table 1. Effect of Compounds 1-10")
    assert all((c.text_raw or "").strip() != "1-10" for c in captions)
    assert "caption_mismatch" in t1.quality_flags
    assert t1.source_ref["caption_text"] == "1-10"
    assert any(i.code == "caption_mismatch" and i.node_id == "p003.g000" for i in ir.quality.issues)


@needs_real
def test_f4_abstract_absorbed_by_merge() -> None:
    ir = real_ir()
    ab = ir.get("p000.b007")
    assert ab.node_type == "paragraph" and "absorbed_by_merge" in ab.quality_flags
    assert ab.parent_id != "p000.g007"  # 不挂在组下
    assert ab.text_raw.startswith("ABSTRACT:")
    toc = ir.get("p000.g007")
    assert toc.node_type == "figure" and toc.subtype == "toc_graphic"
    assert any(r.type == "absorbed_from" and r.source == "p000.b007" for r in ir.relations)


@needs_real
def test_footnotes_and_parts() -> None:
    ir = real_ir()
    t1 = ir.get("p003.g000")
    assert t1.attributes["footnote_marks"]["a"].startswith("Effect over the DA")
    kinds = sorted(ir.get(c).node_type for c in t1.children)
    assert kinds == ["caption", "figure_part", "figure_part", "footnote"]
    s2 = ir.get("p004.b000")  # 单块 scheme：两个 image_caption 子块，一个是标题，一个是脚注
    assert s2.label == "Scheme 2"
    assert sorted(ir.get(c).node_type for c in s2.children) == ["caption", "footnote"]
    assert "caption_mismatch" not in s2.quality_flags


@needs_real
def test_legacy_ids() -> None:
    ir = real_ir()
    assert ir.get("p003.g000").source_ref["legacy_id"] == "tab_p3_0"
    assert ir.get("p001.b003").source_ref["legacy_id"] == "fig_p1_3"


def test_synthetic_group_members() -> None:
    ir = build_ir(
        [
            page(
                1,
                blk(
                    "image_body", [50, 50, 550, 500], None, 0, merged_from=[0, 1, 2, 3], caption_text="16-27"
                ),
                blk("text", [50, 50, 500, 70], 0, None, "Table 2. Effect of compounds"),
                blk(
                    "table_body",
                    [50, 75, 550, 450],
                    1,
                    None,
                    sub_blocks=[{"type": "table_caption", "bbox": [290, 75, 320, 85], "text": "16-27"}],
                ),
                blk("text", [50, 455, 550, 480], 2, None, "aValues are means. bNot determined."),
                blk("text", [50, 485, 200, 495], 3, None, "R1 = Me"),
            )
        ],
        document_id="doc_T",
    )
    g = ir.get("p001.g000")
    assert g.node_type == "table" and g.label == "Table 2" and "caption_mismatch" in g.quality_flags
    children = {c: ir.get(c) for c in g.children}
    assert {n.node_type for n in children.values()} == {"caption", "figure_part", "footnote"}
    assert g.attributes["footnote_marks"] == {"a": "Values are means.", "b": "Not determined."}
    assert any(n.subtype == "text" and n.text_raw == "R1 = Me" for n in children.values())


def test_synthetic_absorbed_long_text() -> None:
    ir = build_ir(
        [
            page(
                2,
                blk("image_body", [50, 50, 550, 500], None, 0, merged_from=[0, 1]),
                blk("text", [50, 50, 550, 200], 0, None, LONG),
                blk("image_body", [50, 210, 550, 500], 1, None),
            )
        ],
        document_id="doc_T",
    )
    assert "absorbed_by_merge" in ir.get("p002.b000").quality_flags
    assert ir.get("p002.g000").node_type == "figure" and ir.get("p002.g000").subtype is None
