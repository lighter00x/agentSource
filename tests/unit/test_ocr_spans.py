"""Formula preview preserves MinerU span boundaries and refuses misaligned source blocks."""

from drugpaper.api.nodes import ocr_attributes
from drugpaper.parsers.mineru_legacy.schemas import load_middle


def test_typed_formula_spans_keep_order_and_alignment() -> None:
    middle = load_middle(
        {
            "pdf_info": [
                {
                    "page_idx": 0,
                    "page_size": [100, 100],
                    "para_blocks": [
                        {
                            "type": "text",
                            "bbox": [0, 0, 80, 20],
                            "lines": [
                                {
                                    "bbox": [0, 0, 80, 20],
                                    "spans": [
                                        {"type": "text", "content": "Energy "},
                                        {"type": "inline_equation", "content": r"E=mc^2"},
                                        {"type": "text", "content": "."},
                                    ],
                                }
                            ],
                        }
                    ],
                }
            ]
        }
    )
    source = {"page_idx": 0, "detailed_index": 0, "bbox_pt": [0, 0, 80, 20]}
    result = ocr_attributes(middle, source)
    assert result["ocr_lines"][0]["spans"] == [
        {"type": "text", "content": "Energy "},
        {"type": "inline_equation", "content": r"E=mc^2"},
        {"type": "text", "content": "."},
    ]
    result = ocr_attributes(middle, {**source, "bbox_pt": [20, 20, 80, 40]})
    assert "ocr_lines" not in result and "ocr_render_warning" in result
