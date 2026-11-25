from __future__ import annotations

import re
from dataclasses import dataclass

import pymupdf

from drugpaper.domain.geometry import BBox, Region, pt_to_norm
from drugpaper.facts.value_parser import normalized


@dataclass(frozen=True)
class Word:
    text: str
    bbox: BBox
    page: int
    line: tuple[int, int]


def words_in_region(pdf: bytes, region: Region) -> list[Word]:
    with pymupdf.open(stream=pdf, filetype="pdf") as document:
        page = document[region.page]
        out = []
        for x0, y0, x1, y1, text, block, line, _word in page.get_text("words"):
            box = pt_to_norm([x0, y0, x1, y1], (page.rect.width, page.rect.height))
            cx, cy = (box.x0 + box.x1) / 2, (box.y0 + box.y1) / 2
            if region.bbox.x0 <= cx <= region.bbox.x1 and region.bbox.y0 <= cy <= region.bbox.y1:
                out.append(Word(text, box, region.page, (block, line)))
        return out


def numeric_tokens(words: list[Word]) -> list[str]:
    lines: dict[tuple[int, int, int], list[Word]] = {}
    for word in words:
        lines.setdefault((word.page, *word.line), []).append(word)
    return [
        normalized(" ".join(w.text for w in sorted(line, key=lambda w: w.bbox.x0)))
        for line in lines.values()
        if any(re.search(r"\d", w.text) for w in line)
    ]
