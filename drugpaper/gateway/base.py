"""ModelClient 协议、请求/响应模型与类型化异常。

所有模型调用方只依赖本模块；P1 的唯一实现是 `openai_compat.OpenAICompatClient`，
P3 起按任务名路由，P7 由模型网关实现同一协议。
"""

from __future__ import annotations

from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field


class TextPart(BaseModel):
    type: Literal["text"] = "text"
    text: str


class ImagePart(BaseModel):
    type: Literal["image"] = "image"
    blob_ref: str | None = None  # P1：本地文件路径；P2 起为 BlobStore 引用
    data_b64: str | None = None
    mime: str = "image/png"


ContentPart = TextPart | ImagePart


class ToolCall(BaseModel):
    id: str
    name: str
    arguments: dict[str, Any] = Field(default_factory=dict)  # 已解析的 JSON
    # 模型给出的参数不是合法 JSON 时：arguments 为空，保留原文并记录错误，交给运行时反馈给模型
    raw_arguments: str | None = None
    arguments_error: str | None = None


class Message(BaseModel):
    role: Literal["system", "user", "assistant", "tool"]
    content: str | list[ContentPart] | None = None
    tool_calls: list[ToolCall] = Field(default_factory=list)
    tool_call_id: str | None = None  # role=tool 时必填
    name: str | None = None
    response_items: list[dict[str, Any]] = Field(default_factory=list)

    def text(self) -> str:
        """纯文本视图（忽略图像部分）。"""
        if self.content is None:
            return ""
        if isinstance(self.content, str):
            return self.content
        return "".join(p.text for p in self.content if isinstance(p, TextPart))


class ToolSchema(BaseModel):
    name: str
    description: str
    parameters: dict[str, Any]  # JSON Schema


class Usage(BaseModel):
    input_tokens: int = 0
    output_tokens: int = 0
    image_count: int = 0

    def __add__(self, other: Usage) -> Usage:
        return Usage(
            input_tokens=self.input_tokens + other.input_tokens,
            output_tokens=self.output_tokens + other.output_tokens,
            image_count=self.image_count + other.image_count,
        )


class ChatRequest(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    messages: list[Message]
    tools: list[ToolSchema] = Field(default_factory=list)
    response_schema: type[BaseModel] | None = None
    temperature: float = 0.0
    max_tokens: int = 2048
    timeout_s: float = 60
    thinking: bool = False


class ChatResponse(BaseModel):
    content: str | None = None
    tool_calls: list[ToolCall] = Field(default_factory=list)
    parsed: BaseModel | None = None  # response_schema 解析结果
    usage: Usage = Field(default_factory=Usage)
    model: str = ""
    latency_ms: int = 0
    response_items: list[dict[str, Any]] = Field(default_factory=list)


# ------------------------------------------------------------------ 异常


class ModelError(Exception):
    """模型调用失败的基类。retryable 表示同一端点值得重试。"""

    retryable: bool = False

    def __init__(self, message: str = "", *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class ModelTimeout(ModelError):
    retryable = True


class RateLimited(ModelError):
    retryable = True


class ModelBadResponse(ModelError):
    """4xx（429 除外）或响应无法解析。"""


class SchemaViolation(ModelError):
    def __init__(self, raw: str, errors: list[str]) -> None:
        super().__init__("结构化输出不符合 Schema：" + "; ".join(errors[:5]))
        self.raw = raw
        self.errors = errors


class ModelUnavailable(ModelError):
    """服务端 5xx、连接失败等。重试耗尽后仍以该类型抛出。"""

    retryable = True


class ModelClient(Protocol):
    async def chat(self, req: ChatRequest, *, task: str = "default") -> ChatResponse:
        """task 参数 P1 忽略；P3 起用于按任务路由，P7 由网关解析到阶段和模型。"""
        ...
