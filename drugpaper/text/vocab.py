"""规范化用的英文词表（来自 wordninja 的词频表，约 12.6 万词，按频率排序）。

只用于两件事：行尾连字符是否合并、粘连词是否拆开。都只作用于 text_norm。
"""

from __future__ import annotations

from functools import lru_cache

import wordninja  # type: ignore[import-untyped]

COMMON_RANK = 30_000  # "常用词"的频率名次上限
_FUNCTION_WORDS = frozenset(
    [
        "a",
        "an",
        "as",
        "at",
        "be",
        "by",
        "do",
        "in",
        "is",
        "it",
        "of",
        "on",
        "or",
        "to",
        "up",
        "we",
        "and",
        "are",
        "but",
        "can",
        "for",
        "has",
        "its",
        "not",
        "the",
        "was",
    ]
)


@lru_cache(maxsize=1)
def _costs() -> dict[str, float]:
    # wordninja 未公开词表接口，读取其默认模型的内部字段；失败时退化为空词表（规则全部不生效）
    try:
        return dict(wordninja.DEFAULT_LANGUAGE_MODEL._wordcost)
    except AttributeError:  # pragma: no cover
        return {}


@lru_cache(maxsize=1)
def _ranks() -> dict[str, int]:
    return {w: i for i, (w, _) in enumerate(sorted(_costs().items(), key=lambda kv: kv[1]))}


def _base_forms(word: str) -> list[str]:
    w = word.lower()
    forms = [w]
    if w.endswith("ies") and len(w) > 4:
        forms.append(w[:-3] + "y")
    if w.endswith("es") and len(w) > 4:
        forms.append(w[:-2])
    if w.endswith("s") and len(w) > 3:
        forms.append(w[:-1])
    return forms


def rank(word: str) -> int | None:
    ranks = _ranks()
    found = [ranks[f] for f in _base_forms(word) if f in ranks]
    return min(found) if found else None


def is_word(word: str) -> bool:
    return rank(word) is not None


def is_common(word: str, limit: int = COMMON_RANK) -> bool:
    r = rank(word)
    return r is not None and r < limit


def should_join_hyphen(left: str, right: str) -> bool:
    """`re-placement` → 合并；`long-term`、`non-covalent` → 保留。

    合并条件：合并后的词在词表中，且比两部分中较罕见的那个更常用（说明连字符来自断行）。
    """
    merged = left + right
    rm = rank(merged)
    if rm is None:
        return False
    rl, rr = rank(left), rank(right)
    if rl is None or rr is None:
        return True
    return rm < max(rl, rr)


def split_glued(word: str) -> tuple[str, str] | None:
    """`uponactivation` → ("upon", "activation")。只做两段拆分，两段都必须是常用词。"""
    w = word.lower()
    if is_word(w):
        return None
    best: tuple[int, tuple[str, str]] | None = None
    for k in range(2, len(w) - 1):
        a, b = w[:k], w[k:]
        if len(a) < 3 and a not in _FUNCTION_WORDS:
            continue
        if len(b) < 3 and b not in _FUNCTION_WORDS:
            continue
        ra, rb = rank(a), rank(b)
        if ra is None or rb is None or ra >= COMMON_RANK or rb >= COMMON_RANK:
            continue
        score = max(ra, rb)
        if best is None or score < best[0]:
            best = (score, (word[:k], word[k:]))
    return best[1] if best else None
