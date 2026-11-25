"""正文抽取、区间提及和 V-E02；拒绝无原文依据的模型字段。"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from drugpaper.capabilities.tokens import estimate_tokens
from drugpaper.domain.nodes import EvidenceRef
from drugpaper.facts.text_models import ExperimentalName, Mention, SpanItem, TextExtraction
from drugpaper.gateway.base import ChatRequest, Message
from drugpaper.skills.protocol import ArtifactDraft, SkillContext, SkillQuality, SkillResult
from drugpaper.text.normalize import Normalized, normalize_detail, normalize_text

EXTRACT_ROLES = frozenset({"abstract", "results", "discussion", "conclusion", "experimental"})
RANGE = re.compile(r"\bcompounds?\s+\$?\s*(\d+)\s*[-−–]\s*(\d+)\b", re.I)
LABEL = r"(?:\([RS]\)-)?\d+[a-z]?"
MENTION = re.compile(
    r"\bcompounds?\s+\$?\s*(?P<label>" + LABEL + r")(?!\w|\.\d)\$?(?:\s*[\[(](?P<alias>UCM-[\w-]+))?", re.I
)
NAME = re.compile(r"^(?P<name>.+?)\s*\((?P<label>\d+[a-z]?|\([RS]\)-\d+)\)\.")


def contains(text: str, value: str) -> bool:
    normalized = normalize_text(value)
    return (
        bool(normalized)
        and re.search(r"(?<![\w.])" + re.escape(normalized) + r"(?!\w|\.\d)", text, re.I) is not None
    )


def contains_label(text: str, label: str) -> bool:
    if contains(text, label):
        return True
    return label.isdecimal() and any(
        int(a) <= int(label) <= int(b) and int(b) - int(a) <= 200 for a, b in RANGE.findall(text)
    )


def rule_mentions(detail: Normalized, ref: str) -> list[Mention]:
    out = []
    for match in MENTION.finditer(detail.norm):
        if any(a <= match.start() < b for a, b in (m.span() for m in RANGE.finditer(detail.norm))):
            continue
        span = match.span()
        raw_span = detail.map_span(*span)
        out.append(
            Mention(
                label=match["label"],
                aliases=[match["alias"]] if match["alias"] else [],
                char_span=span,
                evidence=[EvidenceRef(node_id=ref, char_span=raw_span)],
            )
        )
    for match in RANGE.finditer(detail.norm):
        start, end = map(int, match.groups())
        if 0 <= end - start <= 200:
            out.extend(
                Mention(
                    label=str(label),
                    char_span=match.span(),
                    evidence=[EvidenceRef(node_id=ref, char_span=detail.map_span(*match.span()))],
                )
                for label in range(start, end + 1)
            )
    return list({(item.label, item.char_span): item for item in out}.values())


def validate_extraction(payload: TextExtraction, detail: Normalized, ref: str) -> TextExtraction:
    """原文命中是必要条件；范围内的合理标签不被当作虚构。"""
    result = TextExtraction()
    for field in ("compound_mentions", "measurements", "assay_descriptions", "optimization_statements"):
        for item in getattr(payload, field):
            result.candidate_count += 1
            start, end = item.char_span
            if item.evidence_text:
                # 模型难以精确计数字符。只接受当前正文内唯一的逐字引文定位，
                # 重复引文不能猜位置，修复仍显式标待复核。
                quote = normalize_text(item.evidence_text)
                offset = detail.norm.find(quote) if quote else -1
                if (
                    offset >= 0
                    and detail.norm.find(quote, offset + 1) < 0
                    and detail.norm[start:end] != quote
                ):
                    start, end = offset, offset + len(quote)
                    result.span_repair_count += 1
            if not 0 <= start < end <= len(detail.norm):
                result.discarded_count += 1
                continue
            excerpt = detail.norm[start:end]
            claims: list[str] = []
            labels: list[str] = []
            if field == "compound_mentions":
                labels = [item.label]
                claims = item.aliases
            elif field == "measurements":
                labels = [item.compound_label]
                claims = [
                    item.metric_text,
                    item.value_raw,
                    *([item.unit] if item.unit else []),
                    *([item.conditions] if item.conditions else []),
                ]
            else:
                claims = [item.text]
                if field == "optimization_statements":
                    labels = [value for value in (item.from_label, item.to_label) if value]
            if not all(contains_label(excerpt, label) for label in labels) or not all(
                contains(excerpt, claim) for claim in claims
            ):
                result.discarded_count += 1
                continue
            # 模型自带的 node_id/evidence 不可信，只由代码重建。
            validated: SpanItem = item.model_copy(
                update={
                    "char_span": (start, end),
                    "evidence": [EvidenceRef(node_id=ref, char_span=detail.map_span(start, end))],
                }
            )
            getattr(result, field).append(validated)
    if result.discarded_count or result.span_repair_count:
        result.status = "needs_review"
    return result


def text_chunks(text: str, max_tokens: int = 2800) -> list[tuple[int, str]]:
    """单段过长时优先句末分割，不丢字符；返回段内全局偏移。"""
    if max_tokens < 64:
        raise ValueError("text chunk budget too small")
    chunks = []
    start = 0
    while start < len(text):
        end = min(len(text), start + max_tokens * 4)
        while estimate_tokens(text[start:end]) > max_tokens:
            end = start + (end - start) // 2
        if end < len(text):
            boundary = max(
                text.rfind(". ", start, end), text.rfind("; ", start, end), text.rfind("。", start, end)
            )
            if boundary >= start + (end - start) // 2:
                end = boundary + 1
        chunks.append((start, text[start:end]))
        start = end
    return chunks


async def extract(inputs: dict[str, Any], ctx: SkillContext) -> SkillResult:
    if ctx.materials is None:
        raise ValueError("text_extract需要当前正文节点材料")
    target = ctx.materials.target
    raw = target.get("text_raw")
    if not isinstance(raw, str) or not raw.strip():
        raise ValueError("正文缺少text_raw，无法生成可回跳证据")
    detail = normalize_detail(raw)
    role = target.get("section_role")
    ref = ctx.target_ref
    result = TextExtraction()
    if role == "introduction":
        result.compound_mentions = rule_mentions(detail, ref)
        result.candidate_count = len(result.compound_mentions)
    elif role in EXTRACT_ROLES:
        chunks = text_chunks(detail.norm)
        if len(chunks) > ctx.budget.max_model_calls:
            raise ValueError("正文分块超过Skill模型调用预算")
        for offset, chunk in chunks:
            response = await ctx.model.chat(
                ChatRequest(
                    messages=[
                        Message(role="system", content=ctx.extras.get("skill_prompt", "")),
                        Message(
                            role="user",
                            content=json.dumps(
                                {"node_ref": ref, "section_role": role, "text_norm": chunk},
                                ensure_ascii=False,
                            ),
                        ),
                    ],
                    response_schema=TextExtraction,
                    max_tokens=4096,
                ),
                task="text_extract",
            )
            parsed = TextExtraction.model_validate(
                response.parsed.model_dump() if response.parsed else json.loads(response.content or "{}")
            )
            # 先校验局部分块边界，防止模型以偏移越界引用相邻块。
            for field in (
                "compound_mentions",
                "measurements",
                "assay_descriptions",
                "optimization_statements",
            ):
                for item in getattr(parsed, field):
                    a, b = item.char_span
                    item.char_span = (a + offset, b + offset) if 0 <= a < b <= len(chunk) else (-1, -1)
            validated = validate_extraction(parsed, detail, ref)
            for field in (
                "compound_mentions",
                "measurements",
                "assay_descriptions",
                "optimization_statements",
            ):
                getattr(result, field).extend(getattr(validated, field))
            result.discarded_count += validated.discarded_count
            result.candidate_count += validated.candidate_count
            result.span_repair_count += validated.span_repair_count
        if result.discarded_count or result.span_repair_count:
            result.status = "needs_review"
        # 规则区间与明确的 UCM 别名不依赖模型是否提及。
        existing = {(item.label, item.char_span) for item in result.compound_mentions}
        for item in rule_mentions(detail, ref):
            if (item.label, item.char_span) not in existing:
                result.compound_mentions.append(item)
        if role == "experimental" and (match := NAME.match(detail.norm)):
            result.experimental_names.append(
                ExperimentalName(
                    name=match["name"],
                    label=match["label"],
                    char_span=match.span(),
                    evidence=[EvidenceRef(node_id=ref, char_span=detail.map_span(*match.span()))],
                )
            )
    else:
        result.status = "skipped"
    result.source_text_sha256 = hashlib.sha256(raw.encode()).hexdigest()
    return SkillResult(
        artifacts=[ArtifactDraft(artifact_type="text_extraction", payload=result.model_dump(mode="json"))],
        quality=SkillQuality(
            status="needs_review" if result.discarded_count or result.span_repair_count else "ok",
            warnings=[
                f"V-E02 discarded {result.discarded_count}/{result.candidate_count}; span repairs {result.span_repair_count}"
            ]
            if result.discarded_count or result.span_repair_count
            else [],
        ),
    )
