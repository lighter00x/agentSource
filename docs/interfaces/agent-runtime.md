# 接口冻结：Agent Runtime（P1）

> 状态：**已冻结**（P1 结束，2026-09-30）。修改以下签名或语义需要在合并请求中说明影响范围，并通知依赖方（P2/P3/P7 负责人）。
> 源码为准：下表中的"位置"指向权威定义。

## 1. 总览

| 接口 | 位置 | P1 实现 | 将被替换 / 充实 |
|---|---|---|---|
| `ModelClient` | `drugpaper/gateway/base.py` | `OpenAICompatClient` | P3 按任务名路由；P7 `ModelGateway` |
| `ChatRequest` / `ChatResponse` / `Message` / `ToolCall` / `ToolSchema` / `Usage` | `drugpaper/gateway/base.py` | — | P7 可加字段（只增不改） |
| 模型异常 | `drugpaper/gateway/base.py` | — | — |
| `Tool` / `ToolSpec` / `ToolContext` / `ToolResult` / `@tool` | `drugpaper/tools/protocol.py` | — | P3 增加 version、cost_class 等（只增不改） |
| `ToolRegistry` | `drugpaper/tools/registry.py` | — | — |
| `DocumentSource` / `BlockView` / `Hit` | `drugpaper/docsrc/base.py` | `LayoutJsonSource` | **P2** IR + 数据库 |
| `ToolsetProvider` | `drugpaper/agent/interfaces.py` | `StaticToolset` | **P3** `DisclosureToolset` |
| `ContextAssembler` | `drugpaper/agent/interfaces.py` | `SimpleAssembler` | **P3** `PromptAssembler` |
| `AnswerValidator` / `AnswerSanitizer` | `drugpaper/agent/interfaces.py` | `CitationValidator`、`NoopValidator` | P5/P6 各层校验器 |
| `Tracer` | `drugpaper/agent/interfaces.py` | `JsonlTracer`、`ConsolePrinter` | **P2** RunRecord 入库 |
| Checkpointer | `AgentRuntime(checkpointer=...)` | LangGraph `InMemorySaver` | **P2** Postgres checkpointer |
| `AgentConfig` | `drugpaper/agent/config.py` | — | — |
| `Budget` / `AgentState` / `AgentAnswer` / `Citation` / `AgentRunResult` | `drugpaper/agent/state.py` | — | P5 增加调用纪律计数 |
| `AgentRuntime.run` | `drugpaper/agent/runtime.py` | — | — |

## 2. 模型调用

```python
class ModelClient(Protocol):
    async def chat(self, req: ChatRequest, *, task: str = "default") -> ChatResponse: ...

class ChatRequest(BaseModel):
    messages: list[Message]
    tools: list[ToolSchema] = []
    response_schema: type[BaseModel] | None = None
    temperature: float = 0.0
    max_tokens: int = 2048
    timeout_s: float = 60
    thinking: bool = False

class ChatResponse(BaseModel):
    content: str | None
    tool_calls: list[ToolCall]
    parsed: BaseModel | None          # response_schema 的解析结果
    usage: Usage                      # input_tokens / output_tokens / image_count
    model: str                        # 服务端返回的实际模型名
    latency_ms: int
    response_items: list[dict] = []   # Responses 原生输出项，含加密 reasoning，供续接与恢复

class ToolCall(BaseModel):
    id: str
    name: str
    arguments: dict                   # 已解析的 JSON
    raw_arguments: str | None = None  # 参数不是合法 JSON 时保留原文
    arguments_error: str | None = None
```

语义（所有实现必须遵守）：

- `task`：P1 忽略；P3 起按任务路由，P7 由网关解析到阶段和模型。
- `response_schema` 不为空时：返回的 `parsed` 必须是该模型的实例；校验失败时实现方负责**一次修复重试**（把校验错误作为用户消息附上），仍失败抛 `SchemaViolation(raw, errors)`。
- 工具参数不是合法 JSON 时**不抛异常**：`arguments={}`，填 `raw_arguments` 与 `arguments_error`，由运行时反馈给模型。
- 失败只以类型化异常表达：`ModelTimeout`、`RateLimited`、`ModelBadResponse`、`SchemaViolation`、`ModelUnavailable`（基类 `ModelError`，`retryable` 表示同端点值得重试，`status_code` 可选）。
- `thinking=False` 时关闭思考输出（Qwen：`chat_template_kwargs.enable_thinking=false`，可在 models.yaml 中以 `thinking.toggle: none` 关闭该行为）。

2026-10-01 只增字段补充：`ModelSpec` / `ResolvedModel.api` 可选 `chat_completions`（默认）或 `responses`。`Message.response_items` 与 `ChatResponse.response_items` 默认空列表；AgentRuntime 将响应中的原生输出项写入 assistant 消息并随 checkpoint 持久化，Responses 下一轮回放完整 reasoning / function_call 项，再接 function_call_output。现有 Chat Completions 调用不受影响。

Responses 思考级别通过模型 `extra_body.reasoning.effort` 配置；`thinking` 布尔开关保留用于 Qwen 适配，不映射成 OpenAI 的 reasoning 强度。当前文本问答为 `gpt-6-luna`、`medium`，通过 `/v1/responses`，不发送 temperature。Responses 请求 `store=false`，原生输出项仅用于会话续接；兼容网关返回 SSE 时按 output_item.done 汇总，completed / 输出缺失或 incomplete 时失败，不能判为成功。

## 3. Tool 协议

```python
class ToolSpec(BaseModel):
    name: str
    description: str                  # 短描述（P3 起作为 L0）
    input_model: type[BaseModel]
    output_model: type[BaseModel] | None = None
    side_effect: Literal["read", "compute", "write_fact", "external_model"] = "read"
    timeout_s: float = 10
    max_output_chars: int = 4000
    def to_schema(self) -> ToolSchema   # 去掉 title 等冗余字段

class ToolContext(BaseModel):
    run_id: str
    document_id: str | None = None
    user_id: str = "default"
    trace_id: str
    parent_run_id: str | None = None

class ToolResult(BaseModel):
    ok: bool
    data: dict | None = None
    error: str | None = None
    refs: list[str] = []              # 结果中出现的节点 ID，用于引用校验
    truncated: bool = False

class Tool(ABC):
    spec: ToolSpec
    async def run(self, ctx: ToolContext, args: BaseModel) -> ToolResult: ...

def tool(name, description, *, timeout_s=10, max_output_chars=4000, side_effect="read",
         input_model=None, output_model=None): ...   # 装饰 async (ctx, args: PydanticModel)

class ToolRegistry:
    def register(self, t: Tool) -> None                           # 重名抛 ToolRegistrationError
    def get(self, name: str) -> Tool                              # 不存在抛 KeyError
    def schemas(self, names: list[str]) -> list[ToolSchema]
    async def invoke(self, ctx, call: ToolCall, *, allowed: set[str] | None = None) -> ToolResult
```

`invoke` **永不抛出业务异常**：未知工具（或不在 `allowed` 中）→ `error="unknown tool: <name>"`；参数非法 JSON / 校验失败 → 精简的校验信息；超时 → `error="timeout"`；其他异常 → `"<类型>: <信息>"`（不含堆栈）；输出序列化后超过 `max_output_chars` → `data={"truncated_text": ...}`、`truncated=True`，`refs` 只保留截断后仍可见的 ID。

## 4. 文档数据源

```python
class DocumentSource(Protocol):
    document_id: str                              # 只读属性
    def pages(self) -> list[int]
    def blocks(self, page: int | None = None) -> list[BlockView]
    def block(self, block_id: str) -> BlockView   # 不存在抛 KeyError
    def search_text(self, pattern: str, max_results: int = 10) -> list[Hit]   # IGNORECASE；非法正则按字面量

class BlockView(BaseModel):  id, page, type, kind("block"|"group"), text, bbox(归一化 [0,1]) | None, members
class Hit(BaseModel):        block_id, page, type, snippet
```

块 ID 规则与设计文档 6.2 节一致：原始块 `p{page:03d}.b{detailed_index:03d}`，页内合并组 `p{page:03d}.g{confirmed_index:03d}`。P2 的 IR 实现必须保持同样的 ID，以便已有引用继续有效。

## 5. 扩展点

```python
class ToolsetProvider(Protocol):
    def tools_for(self, state: AgentState) -> list[Tool]         # 不含 submit_answer

class ContextAssembler(Protocol):
    def build(self, state: AgentState) -> list[Message]          # 本轮完整消息

class AnswerValidator(Protocol):
    def validate(self, answer: BaseModel, state: AgentState) -> list[str]   # 空列表 = 通过

@runtime_checkable
class AnswerSanitizer(Protocol):                                  # 可选能力
    def sanitize(self, answer: BaseModel, state: AgentState) -> BaseModel

class Tracer(Protocol):
    def event(self, run_id: str, kind: str, payload: dict) -> None
```

Tracer 事件（`kind`）：`run_start`（agent、trace_id、parent_run_id、document_id、user_input、budget）、`model_call`（phase=agent/convert/force_finalize、task、model、ok、input/output_tokens、latency_ms、tool_calls、structured、content 摘要）、`tool_call`（name、call_id、args 摘要、ok、error、truncated、latency_ms、refs 数、output 摘要）、`validation`（source、ok、issues、retry、attempt）、`budget_exhausted`（reason、where）、`cancelled`（reason）、`run_end`（stop_reason、steps、model_calls、tool_calls、usage、elapsed_ms、error、warnings）。

## 6. AgentConfig 与 AgentRuntime

```python
@dataclass
class AgentConfig:
    name: str
    system_prompt: str
    toolset: ToolsetProvider
    assembler: ContextAssembler | None = None      # None → SimpleAssembler(system_prompt)
    validators: list[AnswerValidator] = []
    answer_model: type[BaseModel] = AgentAnswer
    budget: Budget = Budget()
    task: str = "default"
    temperature: float = 0.0
    max_tokens: int = 2048
    model_timeout_s: float = 60
    max_validation_retries: int = 1

class Budget(BaseModel):
    max_steps: int = 8; max_model_calls: int = 10; max_tool_calls: int = 20; max_wall_time_s: float = 180

class AgentAnswer(BaseModel):  answer: str; citations: list[Citation]; warnings: list[str]
class Citation(BaseModel):     ref: str; quote: str | None

class AgentRuntime:
    def __init__(self, config: AgentConfig, model: ModelClient, registry: ToolRegistry,
                 tracer: Tracer, checkpointer: BaseCheckpointSaver | None = None): ...
    async def run(self, user_input: str, ctx: ToolContext) -> AgentRunResult: ...

class AgentRunResult(BaseModel):
    run_id: str
    answer: dict                  # answer_model.model_dump()
    stop_reason: str              # answered / budget_exhausted / error / validation_failed
    steps: int; model_calls: int; tool_calls: int
    usage: Usage
    seen_refs: list[str]; warnings: list[str]; error: str | None; elapsed_ms: int
```

`run()` 语义：

1. 结束方式：运行时额外提供 `submit_answer` 工具（参数 = `answer_model` 的 JSON Schema）。模型输出纯文本时，再发起 1 次 `response_schema=answer_model` 的结构化请求（计入 `model_calls`）。
2. 一步 = 一次 agent 模型调用 + 工具执行。同一轮多个工具调用并行执行，结果按 `tool_call_id` 顺序返回；同一轮既有 `submit_answer` 又有其他工具时，先执行工具再校验。
3. 预算：`steps`、`model_calls`、`tool_calls`、耗时任一达到上限 → `force_finalize`（只绑定 `submit_answer`，调用 1 次，不受 `max_model_calls` 限制但计数）；仍未作答 → 兜底答案"未能在预算内完成"。`stop_reason=budget_exhausted`。
4. 校验：依次运行 `validators`；有问题且重试次数 < `max_validation_retries` 且预算未尽 → 把问题反馈给模型重答；否则调用 `AnswerSanitizer.sanitize`，问题写入 `warnings`，`stop_reason=validation_failed`。
5. 模型调用失败或结构化转换两次失败 → `stop_reason=error`，`error` 给出明确原因。
6. 整体包在 `asyncio.timeout(max_wall_time_s + 30)` 中；被取消时写 `cancelled` 事件。
7. checkpointer 的 `thread_id = run_id`；状态中只存 JSON 友好的数据（消息为 `Message.model_dump()`）。
