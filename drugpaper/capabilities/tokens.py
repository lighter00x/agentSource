"""token 估算。

没有与模型一致的 tokenizer 时按字符估算：CJK 字符约 1 token/字，其余字符约 4 字符/token（向上取整）。
估算略偏高，用于预算与校验是安全的。可通过 `set_counter` 注入真实 tokenizer。
"""

from __future__ import annotations

import math
import re
from collections.abc import Callable

_CJK = re.compile(r"[\u3000-\u303f\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff\uff00-\uffef]")

_counter: Callable[[str], int] | None = None


def set_counter(fn: Callable[[str], int] | None) -> None:
    global _counter
    _counter = fn


def estimate_tokens(text: str | None) -> int:
    if not text:
        return 0
    if _counter is not None:
        return _counter(text)
    cjk = len(_CJK.findall(text))
    return cjk + math.ceil((len(text) - cjk) / 4)
