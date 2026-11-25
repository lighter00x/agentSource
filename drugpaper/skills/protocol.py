"""Skill 协议（执行方案 S3.0，设计文档 9.2、9.5 节）。

- `SkillManifest`：`manifest.yaml` 的类型化契约；
- `SkillResult` 及其组成（ArtifactDraft / NodeProposal / FactProposal / RelationDraft / SkillQuality）：Skill 的输出；
- `SkillContext`：运行时注入给 Skill 的上下文（受白名单约束的工具调用器、模型、材料、预算）。
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

if TYPE_CHECKING:
    from drugpaper.gateway.base import ModelClient
    from drugpaper.skills.materials import Materials
    from drugpaper.storage.base import BlobStore

SkillKind = Literal["prompt", "python", "graph"]
Trust = Literal["builtin", "sandboxed"]
Scope = Literal["builtin", "system", "user"]
Mode = Literal["pipeline", "agent"]
Audience = Literal["figure_agent", "reader_agent", "lookup", "chat", "codegen"]
Invocation = Literal["tool_call", "action_enum", "internal"]

_STRICT = ConfigDict(extra="forbid")


class Accepts(BaseModel):
    model_config = _STRICT
    node_types: list[str] = Field(default_factory=list)
    subtypes: list[str] = Field(default_factory=list)
    requires_artifacts: list[str] = Field(default_factory=list)
    document: bool = False  # 文档级 Skill（target_ref 为 document_id）


class Produces(BaseModel):
    model_config = _STRICT
    artifact_type: str
    fact_types: list[str] = Field(default_factory=list)  # 允许提议的事实类型
    node_types: list[str] = Field(default_factory=list)  # 允许提议的子节点类型（molecule、chart_panel …）


class PromptImage(BaseModel):
    """prompt 型 Skill 需要的图片材料。"""

    model_config = _STRICT
    source: Literal["target_crop", "first_page", "page"] = "target_crop"
    max_side: int = Field(default=1024, ge=64, le=4096)
    page: int | None = None  # source=page 时


class PromptSpec(BaseModel):
    model_config = ConfigDict(extra="allow")  # 其余键：具名模板（如 structure: prompts/structure.md）
    main: str | None = None  # 主模板（prompt 型必填）
    includes: list[str] = Field(default_factory=list)  # 公共片段，如 common/data_boundary
    images: list[PromptImage] = Field(default_factory=list)
    system: str | None = None  # 可选的系统模板；缺省为公共片段拼接

    def templates(self) -> dict[str, str]:
        out = {"main": self.main} if self.main else {}
        for k, v in (self.model_extra or {}).items():
            if isinstance(v, str):
                out[k] = v
        if self.system:
            out["system"] = self.system
        return out


class Budgets(BaseModel):
    model_config = _STRICT
    max_model_calls: int = Field(default=2, ge=0)
    max_wall_time_s: float = Field(default=120, gt=0)
    max_tokens: int | None = None


class SandboxSpec(BaseModel):
    model_config = _STRICT
    image: str | None = None
    cpu: float = 1
    memory_mb: int = 1024
    network: Literal["none", "egress_proxy"] = "none"


class CacheSpec(BaseModel):
    model_config = _STRICT
    key_fields: list[str] = Field(default_factory=list)
    enabled: bool = True


class Triggers(BaseModel):
    model_config = _STRICT
    image_types: list[str] = Field(default_factory=list)
    check_codes: list[str] = Field(default_factory=list)
    intents: list[str] = Field(default_factory=list)


class DisclosureBudgets(BaseModel):
    model_config = _STRICT
    l0_tokens: int = 40
    l1_tokens: int = 350


class DisclosureSpec(BaseModel):
    model_config = _STRICT
    audiences: list[Audience] = Field(default_factory=list)
    toolkit: str | None = None
    triggers: Triggers = Field(default_factory=Triggers)
    budgets: DisclosureBudgets = Field(default_factory=DisclosureBudgets)
    invocation: Invocation = "internal"


class SkillManifest(BaseModel):
    """`manifest.yaml`。未知键报错（防止拼写错误被静默忽略）。"""

    model_config = _STRICT

    name: str = Field(pattern=r"^[a-z][a-z0-9_]{1,63}$")
    version: str = Field(pattern=r"^\d+\.\d+\.\d+([.-][0-9A-Za-z.]+)?$")
    kind: SkillKind
    trust: Trust = "builtin"
    scope: Scope | None = None  # 由加载位置决定；manifest 中声明时必须一致
    mode: list[Mode] = Field(default=["pipeline"])
    enabled: bool = True  # false：加载与测试照常，但不会自动激活
    auto_activate: bool = True
    override: bool = False  # 覆盖低优先级作用域的同名 Skill
    accepts: Accepts = Field(default_factory=Accepts)
    entry: str | None = None  # python 型：scripts/main.py:run
    prepare: str | None = None  # prompt 型可选：准备模板变量的钩子 scripts/prepare.py:prepare
    postprocess: str | None = None  # prompt 型可选：模型输出后处理钩子
    input_schema: str
    output_schema: str
    produces: Produces
    tools: list[str] = Field(default_factory=list)  # 白名单
    model_tasks: dict[str, str] = Field(default_factory=dict)  # 逻辑名 → 模型任务名（models.yaml tasks）
    prompts: PromptSpec = Field(default_factory=PromptSpec)
    budgets: Budgets = Field(default_factory=Budgets)
    sandbox: SandboxSpec = Field(default_factory=SandboxSpec)
    cache: CacheSpec = Field(default_factory=CacheSpec)
    fallbacks: list[str] = Field(default_factory=list)
    validators: list[str] = Field(default_factory=lambda: ["schema"])
    disclosure: DisclosureSpec = Field(default_factory=DisclosureSpec)
    vision_structuring: bool = False  # 调用 VLM 做结构化（routing.yaml 的 D38 校验用）

    @field_validator("entry", "prepare", "postprocess")
    @classmethod
    def _entry_format(cls, v: str | None) -> str | None:
        if v is not None and (":" not in v or not v.split(":", 1)[0].endswith(".py")):
            raise ValueError("入口格式应为 scripts/xxx.py:function")
        return v

    @property
    def main_task(self) -> str | None:
        """prompt 型 Skill 的模型任务：`model_tasks.main`，否则取第一个。"""
        if "main" in self.model_tasks:
            return self.model_tasks["main"]
        return next(iter(self.model_tasks.values()), None)


# ---------------------------------------------------------------- 输出


class ArtifactDraft(BaseModel):
    artifact_type: str
    payload: dict[str, Any]
    target_ref: str | None = None  # 缺省为本次运行的 target_ref
    schema_name: str | None = None
    schema_version: str | None = None
    confidence: float | None = None
    status: Literal["completed", "needs_review", "failed"] = "completed"
    upstreams: list[str] = Field(default_factory=list)  # 上游 artifact_id 或 "node:<ref>@<hash>"


class NodeProposal(BaseModel):
    """新增子节点（分子、Panel）。只能挂在目标节点之下；local_bbox 相对父节点的某个 region。"""

    local_id: str  # 如 p003.g000.m02
    node_type: str
    parent_local_id: str
    local_bbox: tuple[float, float, float, float]  # 归一化到父 region 的 [0,1]
    parent_region_index: int = 0
    label: str | None = None
    text: str | None = None
    attributes: dict[str, Any] = Field(default_factory=dict)
    status: Literal["active", "removed", "needs_review"] = "active"


class FactProposal(BaseModel):
    fact_type: str  # structure_candidate / measurement / assay / rgroup_assignment …
    payload: dict[str, Any]
    evidence: list[dict[str, Any]] = Field(default_factory=list)


class RelationDraft(BaseModel):
    source: str
    target: str
    type: str
    evidence: dict[str, Any] = Field(default_factory=dict)


class QualityCheck(BaseModel):
    code: str
    ok: bool
    message: str = ""


class SkillQuality(BaseModel):
    status: Literal["ok", "needs_review", "failed"] = "ok"
    checks: list[QualityCheck] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class SkillResult(BaseModel):
    artifacts: list[ArtifactDraft] = Field(default_factory=list)
    node_proposals: list[NodeProposal] = Field(default_factory=list)
    fact_proposals: list[FactProposal] = Field(default_factory=list)
    relation_proposals: list[RelationDraft] = Field(default_factory=list)
    quality: SkillQuality = Field(default_factory=SkillQuality)


# ---------------------------------------------------------------- 上下文


class ToolNotAllowed(PermissionError):
    pass


ToolFn = Callable[..., Awaitable[Any]]


@dataclass
class ToolCaller:
    """受 manifest `tools` 白名单约束的工具调用器（`ctx.tools.call("get_node", node_id=...)`）。"""

    allowed: frozenset[str]
    impls: dict[str, ToolFn] = field(default_factory=dict)

    async def call(self, name: str, **kwargs: Any) -> Any:
        if name not in self.allowed:
            raise ToolNotAllowed(f"Skill 未声明工具 {name!r}（manifest.tools 白名单）")
        fn = self.impls.get(name)
        if fn is None:
            raise ToolNotAllowed(f"工具 {name!r} 在当前运行环境中不可用")
        return await fn(**kwargs)


@dataclass
class SkillBudget:
    max_model_calls: int
    max_wall_time_s: float
    model_calls: int = 0


@dataclass
class SkillContext:
    user_id: str
    document_id: str | None
    revision: int | None
    target_ref: str
    run_id: str
    trace_id: str
    model: ModelClient  # 已按 model_tasks 与预算包装
    tools: ToolCaller
    blobs: BlobStore | None
    budget: SkillBudget
    materials: Materials | None = None
    skill_id: str = ""  # name@version#hash
    extras: dict[str, Any] = field(default_factory=dict)


def manifest_json_schema() -> dict[str, Any]:
    schema = SkillManifest.model_json_schema()
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    schema["title"] = "drugpaper Skill manifest.yaml"
    return schema
