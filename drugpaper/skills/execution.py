"""无持久化的包执行器：注册测试与正式 Runtime 共用同一条执行路径。"""

from __future__ import annotations

import asyncio
import base64
import copy
import importlib
import inspect
import json
import sys
import types
from typing import Any, ClassVar

from jsonschema import Draft202012Validator
from pydantic import BaseModel, RootModel, create_model, model_validator

from drugpaper.capabilities.assembler import PromptAssembler, PromptParts
from drugpaper.gateway.base import ChatRequest, SchemaViolation, TextPart
from drugpaper.render.images import limit_max_side
from drugpaper.skills.loader import LoadedSkill
from drugpaper.skills.prompt_materials import check_evidence_request, document_evidence_images, project_target
from drugpaper.skills.protocol import ArtifactDraft, SkillContext, SkillResult


class PackageOutput(RootModel[dict[str, Any]]):
    """Expose package JSON Schema verbatim, including its local definitions.

    Pydantic json_schema_extra cannot resolve arbitrary package $defs references.
    The runtime validates the actual payload using jsonschema before commit.
    """

    package_schema: ClassVar[dict[str, Any]] = {}
    semantic_model: ClassVar[type[BaseModel] | None] = None

    @model_validator(mode="before")
    @classmethod
    def validate_package(cls, value: Any) -> Any:
        errors = list(Draft202012Validator(cls.package_schema).iter_errors(value))
        if errors:

            def details(error: Any, alternative: bool = False) -> list[str]:
                if error.context:
                    return [message for child in error.context for message in details(child, True)]
                # Nullable alternatives otherwise obscure the actual object-field error.
                if (
                    alternative
                    and error.validator == "type"
                    and error.validator_value == "null"
                    and error.instance is not None
                ):
                    return []
                path = "/" + "/".join(map(str, error.absolute_path))
                message = error.message
                if error.validator == "type":
                    message = f"expected type {error.validator_value}"
                return [f"{path}: {message[:300]}"]

            messages = list(dict.fromkeys(message for error in errors for message in details(error)))
            raise ValueError("package schema: " + "; ".join(messages[:10]))
        if cls.semantic_model is not None:
            from drugpaper.skills.output_integrity import validate_semantic_output

            value = validate_semantic_output(value, cls.semantic_model)
        return value

    @classmethod
    def model_json_schema(cls, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return copy.deepcopy(cls.package_schema)


async def invoke_entry(skill: LoadedSkill, entry: str, *args: Any) -> Any:
    path, function = entry.split(":", 1)
    source = (skill.path / path).resolve()
    if not source.is_relative_to(skill.path.resolve()):
        raise ValueError("入口不在 Skill 包内")
    # 版本化命名空间支持包内相对导入，也保证 dataclass 的 __module__ 可解析。
    namespace = f"drugpaper_skill_{skill.content_hash}"
    if namespace not in sys.modules:
        package = types.ModuleType(namespace)
        package.__path__ = [str(skill.path)]
        sys.modules[namespace] = package
    relative = source.relative_to(skill.path.resolve()).with_suffix("").parts
    module = importlib.import_module(namespace + "." + ".".join(relative))
    fn = getattr(module, function)
    if inspect.iscoroutinefunction(fn):
        return await fn(*args)
    result = await asyncio.to_thread(fn, *args)
    return await result if inspect.isawaitable(result) else result


async def execute(skill: LoadedSkill, inputs: dict[str, Any], ctx: SkillContext) -> SkillResult:
    manifest = skill.manifest
    assert manifest is not None
    if manifest.vision_structuring:

        def reject_chemical_inputs(value: Any, path: str) -> None:
            if isinstance(value, dict):
                for key, item in value.items():
                    if key.lower() in {
                        "smiles",
                        "esmiles",
                        "e_smiles_raw",
                        "smiles_core",
                        "canonical_smiles",
                        "formula",
                        "assembled_molecule",
                    }:
                        raise ValueError(f"视觉Skill输入只能使用mol_id，不得传入识别或计算结构：{path}.{key}")
                    reject_chemical_inputs(item, f"{path}.{key}")
            elif isinstance(value, list):
                for index, item in enumerate(value):
                    reject_chemical_inputs(item, f"{path}[{index}]")

        reject_chemical_inputs(inputs, "inputs")
    if manifest.kind == "python":
        if manifest.prompts.main:
            variables = ctx.materials.variables(inputs) if ctx.materials else {"target": {}, "inputs": inputs}
            variables["target"] = project_target(skill.name, variables.get("target", {}))
            ctx.extras["skill_prompt"] = skill.render("main", **variables)
        assert manifest.entry is not None
        raw = await invoke_entry(skill, manifest.entry, inputs, ctx)
        return raw if isinstance(raw, SkillResult) else SkillResult.model_validate(raw)
    if manifest.kind != "prompt":
        raise ValueError("不支持的 Skill 类型")
    variables = (
        ctx.materials.variables(inputs) if ctx.materials else {"inputs": inputs, "target": {}, **inputs}
    )
    if manifest.prepare:
        variables.update(await invoke_entry(skill, manifest.prepare, inputs, ctx))
    if manifest.vision_structuring or skill.name in {
        "visual_classify",
        "visual_reclassify",
        "figure_self_check",
    }:
        variables["target"] = project_target(skill.name, variables.get("target", {}))
    task_text = skill.render("main", **variables)
    material: list[Any] = [task_text]
    if ctx.materials:
        for index, image in enumerate(ctx.materials.images):
            if manifest.vision_structuring:
                material.append(
                    TextPart(text=f"目标编号图 · 片段 {index}：以图上的分子编号对应 mol_id，不得自行重编号。")
                )
            if manifest.prompts.images and image.data_b64:
                max_side = min(spec.max_side for spec in manifest.prompts.images)
                data, mime = limit_max_side(base64.b64decode(image.data_b64), max_side)
                material.append(
                    image.model_copy(update={"data_b64": base64.b64encode(data).decode(), "mime": mime})
                )
            else:
                material.append(image)
    if manifest.vision_structuring:
        for index, image in enumerate(ctx.extras.get("reference_images", [])):
            material.append(
                TextPart(text=f"辅助原图 · 片段 {index}：用于核对图像细节，mol_id 仍以对应目标编号图为准。")
            )
            if image.data_b64:
                data, mime = limit_max_side(base64.b64decode(image.data_b64), 1600)
                material.append(
                    image.model_copy(update={"data_b64": base64.b64encode(data).decode(), "mime": mime})
                )
    if manifest.vision_structuring and skill.name not in {"structure_audit", "reference_assignment_audit"}:
        material.extend(await document_evidence_images(ctx))
    prompt = PromptAssembler().build(PromptParts(system_rules=[skill.system_text()], task_material=material))
    output_model = create_model(
        f"{manifest.name}_output",
        __base__=PackageOutput,
    )
    output_model.package_schema = skill.output_schema
    if skill.name == "scheme_extract":
        from drugpaper.facts.figure_models import VisualExtraction

        output_model.semantic_model = VisualExtraction
    if manifest.vision_structuring:
        check_evidence_request(ChatRequest(messages=prompt.messages, response_schema=output_model), ctx)
    try:
        response = await ctx.model.chat(
            ChatRequest(messages=prompt.messages, response_schema=output_model),
            task=manifest.main_task or "default",
        )
        payload = (
            response.parsed.model_dump()
            if response.parsed is not None
            else json.loads(response.content or "{}")
        )
    except SchemaViolation as failure:
        if skill.name != "scheme_extract":
            raise
        from drugpaper.facts.reaction_semantics import quarantine_links
        from drugpaper.gateway.openai_compat import parse_structured

        schema_only = create_model("SchemeRecoveryWire", __base__=PackageOutput)
        schema_only.package_schema = skill.output_schema
        # Preserve only structurally valid output; never relax molecule/source constraints.
        raw = parse_structured(failure.raw, schema_only).model_dump()
        try:
            payload = quarantine_links(raw)
        except ValueError:
            # No invalid cross-arrow link: preserve the original field/semantic error.
            raise failure from None
        output_model.model_validate(payload)
    if manifest.postprocess:
        payload = await invoke_entry(skill, manifest.postprocess, payload, ctx)
    return SkillResult(
        artifacts=[
            ArtifactDraft(
                artifact_type=manifest.produces.artifact_type,
                payload=payload,
                confidence=payload.get("confidence"),
                status="needs_review"
                if payload.get("needs_review")
                or payload.get("confidence", 1) < 0.6
                or payload.get("status") in {"unclear", "not_a_table", "failed", "needs_review"}
                or payload.get("semantic_errors")
                else "completed",
            )
        ]
    )
