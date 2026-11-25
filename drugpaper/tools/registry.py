"""ToolRegistry：注册、查找与安全调用。

invoke() 永不抛出业务异常：未知工具、参数错误、超时、工具内部异常都转换为 ToolResult(ok=False)，
交给模型决定是否换参数重试（阶段计划 5 节要点 2）。
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterable

import orjson
from pydantic import ValidationError

from drugpaper.gateway.base import ToolCall, ToolSchema
from drugpaper.tools.protocol import Tool, ToolContext, ToolResult

TRUNCATION_NOTICE = "\n…结果已截断，请缩小范围重查"


class ToolRegistrationError(Exception):
    pass


def _format_validation_error(e: ValidationError, limit: int = 5) -> str:
    parts = []
    for err in e.errors()[:limit]:
        loc = ".".join(str(x) for x in err["loc"]) or "<root>"
        parts.append(f"{loc}: {err['msg']}")
    more = f"（另有 {e.error_count() - limit} 处）" if e.error_count() > limit else ""
    return "参数校验失败：" + "; ".join(parts) + more


def truncate_result(result: ToolResult, max_chars: int) -> ToolResult:
    """序列化后超过 max_chars 时截断 data，并只保留截断后仍可见的 refs。"""
    if result.data is None:
        return result
    text = orjson.dumps(result.data).decode()
    if len(text) <= max_chars:
        return result
    shown = text[:max_chars]
    return result.model_copy(
        update={
            "data": {"truncated_text": shown + TRUNCATION_NOTICE},
            "refs": [r for r in result.refs if r in shown],
            "truncated": True,
        }
    )


class ToolRegistry:
    def __init__(self, tools: Iterable[Tool] = ()) -> None:
        self._tools: dict[str, Tool] = {}
        for t in tools:
            self.register(t)

    def register(self, t: Tool) -> None:
        name = t.spec.name
        if name in self._tools:
            raise ToolRegistrationError(f"工具重名：{name}")
        self._tools[name] = t

    def replace(self, t: Tool) -> None:
        self._tools[t.spec.name] = t

    def get(self, name: str) -> Tool:
        try:
            return self._tools[name]
        except KeyError:
            raise KeyError(f"未注册的工具：{name}") from None

    def names(self) -> list[str]:
        return list(self._tools)

    def __contains__(self, name: object) -> bool:
        return name in self._tools

    def schemas(self, names: list[str]) -> list[ToolSchema]:
        return [self.get(n).spec.to_schema() for n in names]

    async def invoke(
        self, ctx: ToolContext, call: ToolCall, *, allowed: set[str] | None = None, truncate: bool = True
    ) -> ToolResult:
        # 1. 工具不存在（或不在本轮可用工具集中）
        t = self._tools.get(call.name)
        if t is not None and allowed is not None and call.name not in allowed:
            return ToolResult(ok=False, error=f"capability not loaded: {call.name}")
        if t is None:
            return ToolResult(ok=False, error=f"unknown tool: {call.name}")
        # 2. 参数不是合法 JSON（客户端已标记）
        if call.arguments_error:
            return ToolResult(ok=False, error=call.arguments_error)
        # 3. 参数校验
        try:
            args = t.spec.input_model.model_validate(call.arguments)
        except ValidationError as e:
            return ToolResult(ok=False, error=_format_validation_error(e))
        # 4. 带超时执行；5. 其他异常（不含堆栈）
        try:
            result = await asyncio.wait_for(t.run(ctx, args), timeout=t.spec.timeout_s)
        except TimeoutError:
            return ToolResult(ok=False, error="timeout")
        except Exception as e:  # noqa: BLE001 - 工具错误不中断循环
            return ToolResult(ok=False, error=f"{type(e).__name__}: {e}")
        # 6. 截断
        # Trusted Skill code needs complete data; model-facing calls remain bounded.
        return truncate_result(result, t.spec.max_output_chars) if truncate else result
