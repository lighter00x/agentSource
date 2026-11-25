"""P1 的匹配用文本规范化。P2 起委托给 `drugpaper.text.normalize`（完整 text_norm 规则），保留此模块以兼容旧调用方。"""

from __future__ import annotations

from drugpaper.text.normalize import compact_key, normalize_text


def strip_latex(text: str) -> str:
    return normalize_text(text)


__all__ = ["compact_key", "strip_latex"]
