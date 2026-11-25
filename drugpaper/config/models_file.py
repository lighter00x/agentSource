"""读取 config/models.yaml（设计文档 18.2.2 的格式），解析出一次调用所需的模型参数。

解析路径：task → stage → (stage.model 或 stage.pool 的第一个模型) → model → provider。
变量展开：`${VAR}` 与 `env://VAR`，只对实际解析到的字段展开，缺失时抛出 MissingEnvVarError。
"""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Literal

import yaml
from dotenv import dotenv_values
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from drugpaper.config.settings import Settings, get_settings

# ${VAR} 或 ${VAR:-默认值}；默认值可以是字面量，也可以是 $OTHER（引用另一个变量，可级联 $A:-$B）
_VAR_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-([^}]*))?\}")
_ENV_REF_PREFIX = "env://"


class ModelsConfigError(Exception):
    """models.yaml 格式或引用错误。"""


class MissingEnvVarError(ModelsConfigError):
    def __init__(self, var: str, where: str) -> None:
        super().__init__(f"环境变量 {var} 未设置或为空（models.yaml: {where}）")
        self.var = var
        self.where = where


# ------------------------------------------------------------------ 文件模型（宽松，允许 P7 扩充字段）


class _Loose(BaseModel):
    model_config = ConfigDict(extra="allow")


class AuthSpec(_Loose):
    type: Literal["bearer", "none"] = "bearer"
    secret_ref: str | None = None


class ProviderSpec(_Loose):
    type: str = "openai_compatible"
    base_url: str
    auth: AuthSpec = Field(default_factory=AuthSpec)
    timeout_s: float = 120
    trust_env: bool = True
    max_attempts: int = Field(default=3, ge=1, le=10)
    retry_base_delay_s: float = Field(default=1, ge=0)
    retry_max_delay_s: float = Field(default=60, ge=0)
    retry_jitter_s: float = Field(default=0.3, ge=0)
    extra_headers: dict[str, str] = Field(default_factory=dict)


class ThinkingCapability(_Loose):
    toggle: Literal["chat_template_kwargs", "param", "none"] = "none"


class Capabilities(_Loose):
    structured_output: Literal["json_schema", "guided_json", "json_object", "prompt_only"] = "json_schema"
    tool_calling: bool = True
    thinking: ThinkingCapability = Field(default_factory=ThinkingCapability)


class ModelSpec(_Loose):
    provider: str
    model_name: str
    family: str = "generic"
    api: Literal["chat_completions", "responses"] = "chat_completions"
    modalities: list[str] = Field(default_factory=lambda: ["text"])
    capabilities: Capabilities = Field(default_factory=Capabilities)
    limits: dict[str, int] = Field(default_factory=dict)
    concurrency: int = 4
    extra_body: dict[str, Any] = Field(default_factory=dict)


class StageSpec(_Loose):
    fallback_on_retry_exhausted: bool = False
    pool: str | None = None
    model: str | None = None
    requires: list[str] = Field(default_factory=list)


class TaskSpec(_Loose):
    stage: str
    temperature: float = 0.0
    max_tokens: int = 2048
    timeout_s: float = 60
    retry_timeout_multiplier: float = Field(default=1, ge=1, le=10)
    retry_timeout_max_s: float | None = Field(default=None, gt=0)
    thinking: bool = False
    image_max_side: int | None = None


class ModelsFile(_Loose):
    schema_version: int = 1
    providers: dict[str, ProviderSpec]
    models: dict[str, ModelSpec]
    pools: dict[str, list[str]] = Field(default_factory=dict)
    stages: dict[str, StageSpec]
    tasks: dict[str, TaskSpec]


class ResolvedModel(BaseModel):
    """一次调用所需的全部模型参数（已展开变量）。"""

    base_url: str
    api_key: str
    trust_env: bool = True
    max_attempts: int = Field(default=3, ge=1, le=10)
    retry_base_delay_s: float = Field(default=1, ge=0)
    retry_max_delay_s: float = Field(default=60, ge=0)
    retry_jitter_s: float = Field(default=0.3, ge=0)
    model_name: str
    extra_body: dict[str, Any] = Field(default_factory=dict)
    timeout_s: float = 60
    retry_timeout_multiplier: float = Field(default=1, ge=1, le=10)
    retry_timeout_max_s: float | None = Field(default=None, gt=0)
    # 以下字段供客户端适配使用
    model_key: str = ""
    provider_key: str = ""
    family: str = "generic"
    api: Literal["chat_completions", "responses"] = "chat_completions"
    structured_output: str = "json_schema"
    thinking_toggle: str = "none"
    extra_headers: dict[str, str] = Field(default_factory=dict)
    temperature: float = 0.0
    max_tokens: int = 2048
    # P3：任务名路由
    task: str = "default"
    stage: str = ""
    thinking: bool = False
    image_max_side: int | None = None
    modalities: list[str] = Field(default_factory=lambda: ["text"])


# ------------------------------------------------------------------ 加载与解析


def load_models_file(path: Path | str) -> ModelsFile:
    p = Path(path)
    if not p.exists():
        raise ModelsConfigError(f"找不到模型配置文件：{p}")
    try:
        raw = yaml.safe_load(p.read_text(encoding="utf-8"))
    except yaml.YAMLError as e:
        raise ModelsConfigError(f"models.yaml 不是合法的 YAML：{e}") from e
    if not isinstance(raw, dict):
        raise ModelsConfigError("models.yaml 顶层必须是映射")
    try:
        return ModelsFile.model_validate(raw)
    except ValidationError as e:
        raise ModelsConfigError(f"models.yaml 字段校验失败：{e}") from e


def default_env(dotenv_path: Path | str = ".env") -> dict[str, str]:
    """环境变量来源：.env 文件在下，进程环境变量在上（后者覆盖前者）。"""
    merged: dict[str, str] = {k: v for k, v in dotenv_values(dotenv_path).items() if v is not None}
    merged.update(os.environ)
    return merged


def _expand(value: str, env: Mapping[str, str], where: str) -> str:
    if value.startswith(_ENV_REF_PREFIX):
        var = value[len(_ENV_REF_PREFIX) :]
        got = env.get(var, "")
        if not got:
            raise MissingEnvVarError(var, where)
        return got

    def repl(m: re.Match[str]) -> str:
        var, default = m.group(1), m.group(2)
        got = env.get(var, "")
        if got:
            return got
        if default is None:
            raise MissingEnvVarError(var, where)
        # 默认值：`$OTHER:-$THIRD:-literal` 依次尝试
        for alt in default.split(":-"):
            alt = alt.strip()
            if alt.startswith("$"):
                val = env.get(alt[1:], "")
                if val:
                    return val
            elif alt:
                return alt
        raise MissingEnvVarError(var, where)

    return _VAR_RE.sub(repl, value)


def resolve_task(
    file: ModelsFile, task: str, env: Mapping[str, str], *, candidate: str | None = None
) -> ResolvedModel:
    if task not in file.tasks:
        raise ModelsConfigError(f"任务 {task!r} 未在 models.yaml 的 tasks 中定义")
    t = file.tasks[task]
    if t.stage not in file.stages:
        raise ModelsConfigError(f"任务 {task!r} 引用的阶段 {t.stage!r} 不存在")
    stage = file.stages[t.stage]

    if stage.model:
        model_key = stage.model
    elif stage.pool:
        pool = file.pools.get(stage.pool)
        if not pool:
            raise ModelsConfigError(f"阶段 {t.stage!r} 引用的池 {stage.pool!r} 不存在或为空")
        model_key = pool[0]  # 返回主模型；TaskRouter按显式阶段策略解析备用模型。
    else:
        raise ModelsConfigError(f"阶段 {t.stage!r} 必须指定 model 或 pool")

    if candidate is not None:
        allowed = [stage.model] if stage.model else file.pools.get(stage.pool or "", [])
        if candidate not in allowed:
            raise ModelsConfigError(f"模型 {candidate!r} 不属于阶段 {t.stage!r}")
        model_key = candidate
    if model_key not in file.models:
        raise ModelsConfigError(f"模型 {model_key!r} 未定义")
    m = file.models[model_key]
    if m.provider not in file.providers:
        raise ModelsConfigError(f"模型 {model_key!r} 引用的 provider {m.provider!r} 不存在")
    p = file.providers[m.provider]

    api_key = ""
    if p.auth.type == "bearer":
        if not p.auth.secret_ref:
            raise ModelsConfigError(f"provider {m.provider!r} 的 auth.secret_ref 未配置")
        api_key = _expand(p.auth.secret_ref, env, f"providers.{m.provider}.auth.secret_ref")

    return ResolvedModel(
        base_url=_expand(p.base_url, env, f"providers.{m.provider}.base_url"),
        api_key=api_key or "EMPTY",
        trust_env=p.trust_env,
        max_attempts=p.max_attempts,
        retry_base_delay_s=p.retry_base_delay_s,
        retry_max_delay_s=p.retry_max_delay_s,
        retry_jitter_s=p.retry_jitter_s,
        model_name=_expand(m.model_name, env, f"models.{model_key}.model_name"),
        extra_body=dict(m.extra_body),
        timeout_s=t.timeout_s,
        retry_timeout_multiplier=t.retry_timeout_multiplier,
        retry_timeout_max_s=t.retry_timeout_max_s,
        model_key=model_key,
        provider_key=m.provider,
        family=m.family,
        api=m.api,
        structured_output=m.capabilities.structured_output,
        thinking_toggle=m.capabilities.thinking.toggle,
        extra_headers=dict(p.extra_headers),
        temperature=t.temperature,
        max_tokens=t.max_tokens,
        task=task,
        stage=t.stage,
        thinking=t.thinking,
        image_max_side=t.image_max_side,
        modalities=list(m.modalities),
    )


def load_default_model(
    settings: Settings | None = None,
    *,
    task: str = "default",
    env: Mapping[str, str] | None = None,
) -> ResolvedModel:
    s = settings or get_settings()
    file = load_models_file(s.models_config_path)
    return resolve_task(file, task, env if env is not None else default_env())
