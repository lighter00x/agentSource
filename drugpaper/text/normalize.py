"""文本规范化与 raw↔norm 偏移映射（执行方案 S2.7，设计文档 7.4 节）。

`text_norm` 用于检索和 grep，`text_raw` 用于展示和证据。规范化由一串"替换规则"组成，
每个字符都记录它来自原文的哪一段 `[start, end)`：

- 保留下来的原文字符映射到它自己的位置；
- 规则插入的字符（如 `\\mu` → `μ`）映射到整个被替换的原文片段。

于是 norm 上的任意区间都能通过 `map_span` 映射回 raw 上的区间，作为证据的 `char_span`。
规则是确定性的：同一 raw 在同一 `NORM_VERSION` 下总得到相同的 norm 与偏移，所以偏移不必入库。
"""

from __future__ import annotations

import unicodedata
from collections.abc import Callable
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import regex as re
import yaml

from drugpaper.text import vocab

NORM_VERSION = "2"
RULES_PATH = Path(__file__).with_name("latex_rules.yaml")

# 替换结果：int = 保留当前串中该位置的字符（连同映射）；str = 插入的字面量（映射到整个匹配）
Piece = int | str
ReplFn = Callable[["re.Match[str]"], list[Piece]]


@dataclass
class Normalized:
    raw: str
    norm: str
    starts: list[int]  # norm 第 i 个字符对应 raw 的起点
    ends: list[int]  # norm 第 i 个字符对应 raw 的终点（不含）

    def map_span(self, start: int, end: int) -> tuple[int, int]:
        """norm 区间 [start, end) → raw 区间。空区间映射为 raw 上的插入点。"""
        if not self.norm:
            return (0, 0)
        start = max(0, min(start, len(self.norm)))
        end = max(start, min(end, len(self.norm)))
        if start == end:
            pos = self.starts[start] if start < len(self.norm) else len(self.raw)
            return (pos, pos)
        return (min(self.starts[start:end]), max(self.ends[start:end]))


class _Buffer:
    """字符串 + 每个字符的 raw 映射。"""

    def __init__(self, raw: str) -> None:
        self.text = raw
        self.starts = list(range(len(raw)))
        self.ends = list(range(1, len(raw) + 1))

    def sub(self, pattern: re.Pattern[str], repl: ReplFn) -> bool:
        pieces_text: list[str] = []
        new_starts: list[int] = []
        new_ends: list[int] = []
        pos = 0
        changed = False
        for m in pattern.finditer(self.text):
            s, e = m.span()
            if s < pos:
                continue
            pieces_text.append(self.text[pos:s])
            new_starts.extend(self.starts[pos:s])
            new_ends.extend(self.ends[pos:s])
            out = repl(m)
            if s < e:
                m_start, m_end = self.starts[s], self.ends[e - 1]
            else:  # 零宽匹配：插入点
                m_start = m_end = (
                    self.starts[s] if s < len(self.text) else (self.ends[-1] if self.ends else 0)
                )
            for p in out:
                if isinstance(p, int):
                    pieces_text.append(self.text[p])
                    new_starts.append(self.starts[p])
                    new_ends.append(self.ends[p])
                else:
                    for ch in p:
                        pieces_text.append(ch)
                        new_starts.append(m_start)
                        new_ends.append(m_end)
            if not (len(out) == e - s and all(isinstance(p, int) and p == s + i for i, p in enumerate(out))):
                changed = True
            pos = e
        if not changed:
            return False
        pieces_text.append(self.text[pos:])
        new_starts.extend(self.starts[pos:])
        new_ends.extend(self.ends[pos:])
        self.text = "".join(pieces_text)
        self.starts, self.ends = new_starts, new_ends
        return True


def _keep_group(m: re.Match[str], g: int, *, drop_space: bool = False) -> list[Piece]:
    s, e = m.span(g)
    if drop_space:
        return [i for i in range(s, e) if not m.string[i].isspace()]
    return list(range(s, e))


def _literal(text: str) -> ReplFn:
    return lambda m: [text]


@lru_cache(maxsize=1)
def _rules() -> list[tuple[re.Pattern[str], ReplFn]]:
    cfg: dict[str, Any] = yaml.safe_load(RULES_PATH.read_text(encoding="utf-8"))
    symbols: dict[str, str] = cfg.get("symbols", {})
    fmt: list[str] = cfg.get("format_commands", [])
    escapes: dict[str, str] = cfg.get("escapes", {})
    rules: list[tuple[re.Pattern[str], ReplFn]] = []

    # 0. 控制字符 → 空格；各类连字符在后面统一
    rules.append((re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]"), _literal(" ")))
    # 1. 转义字符 \% \, 等
    esc_pat = re.compile(r"\\([" + re.escape("".join(escapes)) + r"])")
    rules.append((esc_pat, lambda m: [escapes[m.group(1)]] if escapes[m.group(1)] else []))
    # 2. 符号命令
    # 命令后允许紧跟大写字母（MinerU 常见的 `\bulletSupporting`）
    sym_pat = re.compile(
        r"\\(" + "|".join(sorted(map(re.escape, symbols), key=len, reverse=True)) + r")(?![a-z])\s*"
    )

    def sym_repl(m: re.Match[str]) -> list[Piece]:
        out = symbols[m.group(1)]
        # \times 等二元符号后面保留一个空格，避免与后续数字粘连
        tail = m.group(0)[len(m.group(1)) + 1 :]
        return [out + (" " if tail and out in "×±∓≤≥≈→←·" else "")]

    rules.append((sym_pat, sym_repl))
    # 3. 格式命令：去掉命令（其后的 {..} 由花括号规则处理）
    rules.append((re.compile(r"\\(?:" + "|".join(map(re.escape, fmt)) + r")(?![a-z])\s*"), lambda m: []))
    # 4. 其余未知命令：丢弃（只吃掉"首字母 + 小写串"，不吞掉粘连在后面的大写开头单词）
    rules.append((re.compile(r"\\[A-Za-z][a-z]*\s*"), lambda m: []))
    return rules


# 花括号结构（循环处理嵌套）
_SUP = re.compile(r"\s*\^\s*\{\s*([^{}]*?)\s*\}")
_SUB = re.compile(r"\s*_\s*\{\s*([^{}]*?)\s*\}")
_BRACE = re.compile(r"\{\s*([^{}]*?)\s*\}")
_TILDE = re.compile(r"~")
_DASH = re.compile(r"[\u2010\u2011\u2012\u2013\u2014\u2212\u00ad]")
_WS = re.compile(r"\s+")

# 行尾连字符：xxx-yyy（两侧都是小写字母）
_HYPHEN = re.compile(r"(?<![A-Za-z\-])([a-z]{2,})-([a-z]{2,})(?![A-Za-z\-])")
# 缺空格
_WORD_DIGIT = re.compile(r"(?<![A-Za-z])([a-z]{4,})(\d)")
_DIGIT_WORD = re.compile(r"(\d)([a-z]{3,})(?![A-Za-z])")
_LOWER_TITLE = re.compile(r"(?<![A-Za-z])([A-Z]?[a-z]{2,}|a)([A-Z](?:[a-z]{2,}|\d))")
# 公式外的逐字符空格数字：`0 . 4 6` → `0.46`（至少 3 个单字符记号）
_SPACED_NUMBER = re.compile(r"(?<![\w.,\-])(?:[\d.,\-] ){2,}[\d.,](?![^\s^),;])")
# 受体写法 `D1 R` → `D1R`（MinerU 把 \mathbf{D}_{1}\mathbf{R} 拆成两段）
_RECEPTOR = re.compile(r"(?<![A-Za-z])([A-Z]\d{1,2}[A-Za-z]?) (R)(?![A-Za-z])")
_LONG_WORD = re.compile(r"(?<![A-Za-z])([a-z]{7,})(?![A-Za-z])")
_DEGREE_C = re.compile(r"° C(?![A-Za-z])")


def _sup_repl(m: re.Match[str]) -> list[Piece]:
    inner = m.group(1).strip()
    if inner in {"′", "″", "°", "*", "+", "−", "-"} or not inner:
        return _keep_group(m, 1, drop_space=True)
    return ["^", *_keep_group(m, 1, drop_space=True)]


def _join_single_char_tokens(m: re.Match[str], g: int) -> list[Piece]:
    """公式内 MinerU 会在每个字符之间插空格（`4 5 - 4 7`）：两侧都是单字符的空白被删掉。"""
    s, e = m.span(g)
    text = m.string
    out: list[Piece] = []
    i = s
    while i < e:
        if not text[i].isspace():
            out.append(i)
            i += 1
            continue
        j = i
        while j < e and text[j].isspace():
            j += 1
        # 前一段与后一段的长度
        a = i
        while a > s and not text[a - 1].isspace():
            a -= 1
        b = j
        while b < e and not text[b].isspace():
            b += 1
        if not (i - a == 1 and b - j == 1):
            out.extend(range(i, j))
        i = j
    return out


def _brace_repl(m: re.Match[str]) -> list[Piece]:
    return _join_single_char_tokens(m, 1)


def _spaced_number_repl(m: re.Match[str]) -> list[Piece]:
    return [i for i in range(m.start(), m.end()) if not m.string[i].isspace()]


def _hyphen_repl(m: re.Match[str]) -> list[Piece]:
    left, right = m.group(1), m.group(2)
    if vocab.should_join_hyphen(left, right):
        return [*_keep_group(m, 1), *_keep_group(m, 2)]
    return list(range(m.start(), m.end()))


def _space_between(m: re.Match[str], cond: bool) -> list[Piece]:
    if not cond:
        return list(range(m.start(), m.end()))
    return [*_keep_group(m, 1), " ", *_keep_group(m, 2)]


def _lower_title_repl(m: re.Match[str]) -> list[Piece]:
    left, right = m.group(1), m.group(2)
    # `aNeuroblastoma`：左侧是冠词 a 时，只在右侧是较长的词时拆开
    ok = (len(right) >= 5 and right[1:].islower()) if left == "a" else vocab.is_common(left)
    return _space_between(m, ok)


def _long_word_repl(m: re.Match[str]) -> list[Piece]:
    word = m.group(1)
    split = vocab.split_glued(word)
    if split is None:
        return list(range(m.start(), m.end()))
    k = m.start(1) + len(split[0])
    return [*range(m.start(1), k), " ", *range(k, m.end(1))]


def _nfkc_repl(m: re.Match[str]) -> list[Piece]:
    ch = m.group(0)
    out = unicodedata.normalize("NFKC", ch)
    return [m.start()] if out == ch else [out]


_NON_ASCII = re.compile(r"[^\x00-\x7f]")


def normalize_detail(raw: str) -> Normalized:
    buf = _Buffer(raw)
    buf.sub(_NON_ASCII, _nfkc_repl)
    for pat, fn in _rules():
        buf.sub(pat, fn)
    for _ in range(6):
        changed = buf.sub(_SUP, _sup_repl)
        changed = buf.sub(_SUB, lambda m: _keep_group(m, 1, drop_space=True)) or changed
        changed = buf.sub(_BRACE, _brace_repl) or changed
        if not changed:
            break
    buf.sub(_TILDE, _literal(" "))
    buf.sub(_HYPHEN, _hyphen_repl)
    buf.sub(_DASH, _literal("-"))
    buf.sub(_WORD_DIGIT, lambda m: _space_between(m, vocab.is_word(m.group(1))))
    buf.sub(_DIGIT_WORD, lambda m: _space_between(m, vocab.is_common(m.group(2))))
    buf.sub(_LOWER_TITLE, _lower_title_repl)
    buf.sub(_SPACED_NUMBER, _spaced_number_repl)
    buf.sub(_RECEPTOR, lambda m: [*_keep_group(m, 1), *_keep_group(m, 2)])
    buf.sub(_LONG_WORD, _long_word_repl)
    buf.sub(_WS, _literal(" "))
    buf.sub(_DEGREE_C, lambda m: [m.start(), m.end() - 1])
    # 去掉首尾空白
    text, starts, ends = buf.text, buf.starts, buf.ends
    a, b = 0, len(text)
    while a < b and text[a] == " ":
        a += 1
    while b > a and text[b - 1] == " ":
        b -= 1
    return Normalized(raw=raw, norm=text[a:b], starts=starts[a:b], ends=ends[a:b])


def normalize(raw: str) -> tuple[str, list[int]]:
    """返回 (norm, offsets)：offsets[i] 为 norm 第 i 个字符在 raw 中的起点，末尾追加 len(raw)。"""
    d = normalize_detail(raw)
    return d.norm, [*d.starts, len(raw)]


def normalize_text(raw: str | None) -> str:
    return normalize_detail(raw).norm if raw else ""


def map_span(norm_span: tuple[int, int], detail: Normalized) -> tuple[int, int]:
    return detail.map_span(*norm_span)


_COMPACT_DROP = re.compile(r"[\s\-]+")


def compact_key(text: str) -> str:
    """子串比较用的键：规范化后忽略大小写、空白与连字符。"""
    key: str = _COMPACT_DROP.sub("", normalize_text(text))
    return key.casefold()
