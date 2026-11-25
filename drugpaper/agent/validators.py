"""CitationValidator：设计文档 12.5 节 citation_check 的最小版本。

规则：
1. 每个 citation.ref 必须出现在本次运行的工具结果中（seen_refs）；
2. quote 不为空时，必须是对应块文本的子串（大小写、空白归一后比较）；
3. 宽松规则：答案非空但没有任何引用时报问题。
重答仍不通过时，运行时调用 sanitize() 删除无效引用。
"""

from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel

from drugpaper.agent.state import AgentState, seen_ref_set
from drugpaper.docsrc.base import DocumentSource
from drugpaper.docsrc.textnorm import compact_key

_WS = re.compile(r"\s+")


def normalize_for_match(text: str) -> str:
    return _WS.sub(" ", text).strip().casefold()


def quote_matches(quote: str, block_text: str) -> bool:
    """quote 是否为块原文的子串。先做大小写/空白归一比较；不通过时再用去 LaTeX、去空白与连字符的键比较，
    以容忍模型把 `\\mathrm { D } _ { 1 }`、`re-placement`、`uponactivation` 写成正常文本。"""
    if normalize_for_match(quote) in normalize_for_match(block_text):
        return True
    key = compact_key(quote)
    return bool(key) and key in compact_key(block_text)


class CitationValidator:
    def __init__(self, source: DocumentSource | None = None, *, require_citation: bool = True) -> None:
        self._source = source
        self._require_citation = require_citation

    def _citation_issue(self, c: Any, seen: set[str]) -> str | None:
        ref = getattr(c, "ref", None)
        if not ref or ref not in seen:
            return f"引用 {ref} 未出现在工具结果中"
        quote = getattr(c, "quote", None)
        if quote and self._source is not None:
            try:
                block_text = self._source.block(ref).text
            except KeyError:
                return f"引用 {ref} 在文档中不存在"
            if not quote_matches(quote, block_text):
                return f"引用 {ref} 的 quote 不是该块原文的子串：{quote[:60]}"
        return None

    def validate(self, answer: BaseModel, state: AgentState) -> list[str]:
        citations = getattr(answer, "citations", None)
        if citations is None:  # 答案模型不含引用字段，本校验器不适用
            return []
        seen = seen_ref_set(state)
        issues = [i for c in citations if (i := self._citation_issue(c, seen)) is not None]
        text = str(getattr(answer, "answer", "") or "")
        if self._require_citation and text.strip() and not citations:
            issues.append("答案没有任何引用：每个事实性陈述都需要引用工具结果中的块 ID")
        return issues

    def sanitize(self, answer: BaseModel, state: AgentState) -> BaseModel:
        citations = getattr(answer, "citations", None)
        if not citations:
            return answer
        seen = seen_ref_set(state)
        valid = [c for c in citations if self._citation_issue(c, seen) is None]
        if len(valid) == len(citations):
            return answer
        return answer.model_copy(update={"citations": valid})
