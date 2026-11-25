"""Tool 协议：ToolSpec / Tool / ToolContext / ToolResult，以及 @tool 装饰器。

约定：工具的第一个参数是 ToolContext，第二个参数是 Pydantic 输入模型；返回 ToolResult
（也可以返回 Pydantic 模型或 dict，由装饰器包装成 ok=True 的 ToolResult）。
"""

from __future__ import annotations

import inspect
from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
from typing import Any, Literal, get_type_hints

import orjson
from pydantic import BaseModel, ConfigDict, Field

from drugpaper.gateway.base import ToolSchema

SideEffect = Literal["read", "compute", "write_fact", "external_model"]

# JSON Schema 中对模型无用、只增加 token 的键
_SCHEMA_NOISE_KEYS = ("title",)


def compact_json_schema(schema: Any) -> Any:
    """去掉 title 等冗余字段。只删除值为字符串的 title，避免误删名为 "title" 的属性。"""
    if isinstance(schema, dict):
        return {
            k: compact_json_schema(v)
            for k, v in schema.items()
            if not (k in _SCHEMA_NOISE_KEYS and isinstance(v, str))
        }
    if isinstance(schema, list):
        return [compact_json_schema(x) for x in schema]
    return schema


class ToolSpec(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    name: str
    description: str  # 短描述（P3 起作为 L0）
    input_model: type[BaseModel]
    parameters_schema: dict[str, Any] | None = None
    output_model: type[BaseModel] | None = None
    side_effect: SideEffect = "read"
    timeout_s: float = 10
    max_output_chars: int = 4000  # 超出截断，并提示"结果已截断，请缩小范围重查"

    def to_schema(self) -> ToolSchema:
        params = compact_json_schema(self.parameters_schema or self.input_model.model_json_schema())
        params.setdefault("type", "object")
        params.setdefault("properties", {})
        return ToolSchema(name=self.name, description=self.description, parameters=params)


class ToolContext(BaseModel):
    capability_state: dict[str, Any] = Field(default_factory=dict)
    run_id: str
    document_id: str | None = None
    user_id: str = "default"
    trace_id: str
    parent_run_id: str | None = None


class ToolResult(BaseModel):
    ok: bool
    data: dict[str, Any] | None = None
    error: str | None = None
    refs: list[str] = Field(default_factory=list)  # 结果中出现的节点 ID，用于引用校验
    truncated: bool = False

    def to_message_content(self) -> str:
        """作为 role=tool 消息内容发给模型（refs 已体现在 data 中，不重复发送）。"""
        payload: dict[str, Any] = {"ok": self.ok}
        if self.data is not None:
            payload["data"] = self.data
        if self.error:
            payload["error"] = self.error
        if self.truncated:
            payload["truncated"] = True
        return orjson.dumps(payload).decode()


class Tool(ABC):
    spec: ToolSpec

    @abstractmethod
    async def run(self, ctx: ToolContext, args: BaseModel) -> ToolResult: ...


ToolFn = Callable[[ToolContext, Any], Awaitable[ToolResult | BaseModel | dict[str, Any]]]


def to_tool_result(value: ToolResult | BaseModel | dict[str, Any]) -> ToolResult:
    if isinstance(value, ToolResult):
        return value
    if isinstance(value, BaseModel):
        return ToolResult(ok=True, data=value.model_dump(mode="json"))
    return ToolResult(ok=True, data=value)


class FunctionTool(Tool):
    """由 async 函数生成的 Tool。"""

    def __init__(self, spec: ToolSpec, fn: ToolFn) -> None:
        self.spec = spec
        self._fn = fn

    async def run(self, ctx: ToolContext, args: BaseModel) -> ToolResult:
        return to_tool_result(await self._fn(ctx, args))

    def __repr__(self) -> str:
        return f"FunctionTool({self.spec.name!r})"


def _infer_input_model(fn: Callable[..., Any]) -> type[BaseModel]:
    params = list(inspect.signature(fn).parameters)
    if len(params) < 2:
        raise TypeError(f"工具函数 {fn.__name__} 需要 (ctx, args) 两个参数")
    hints = get_type_hints(fn)
    model = hints.get(params[1])
    if not (isinstance(model, type) and issubclass(model, BaseModel)):
        raise TypeError(f"工具函数 {fn.__name__} 的第二个参数必须标注为 Pydantic 模型")
    return model


def tool(
    name: str,
    description: str,
    *,
    timeout_s: float = 10,
    max_output_chars: int = 4000,
    side_effect: SideEffect = "read",
    input_model: type[BaseModel] | None = None,
    output_model: type[BaseModel] | None = None,
) -> Callable[[ToolFn], FunctionTool]:
    """装饰器：从 async 函数的第一个参数（ctx）和第二个参数（Pydantic 输入模型）生成 Tool。"""

    def deco(fn: ToolFn) -> FunctionTool:
        if not inspect.iscoroutinefunction(fn):
            raise TypeError(f"工具函数 {getattr(fn, '__name__', fn)} 必须是 async 函数")
        spec = ToolSpec(
            name=name,
            description=description,
            input_model=input_model or _infer_input_model(fn),
            output_model=output_model,
            side_effect=side_effect,
            timeout_s=timeout_s,
            max_output_chars=max_output_chars,
        )
        return FunctionTool(spec, fn)

    return deco
