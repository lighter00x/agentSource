"""会话能力状态保存在 AgentState，支持 checkpoint、LRU 与 4 轮自动卸载。"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from drugpaper.agent.state import AgentState
from drugpaper.capabilities.index import META_NAMES, CapabilityIndex
from drugpaper.domain.ids import new_id
from drugpaper.persistence.models import CapabilityDisclosureRow
from drugpaper.tools.protocol import Tool, ToolContext, ToolResult
from drugpaper.tools.registry import ToolRegistry


class DisclosureToolset:
    def __init__(
        self,
        registry: ToolRegistry,
        core: list[str],
        index: CapabilityIndex,
        *,
        audience: str = "chat",
        max_loaded: int = 6,
        idle_rounds: int = 4,
    ) -> None:
        self.registry, self.core, self.index = registry, core, index
        self.audience, self.max_loaded, self.idle_rounds = audience, max_loaded, idle_rounds

    def tools_for(self, state: AgentState) -> list[Tool]:
        capstate = state.get("capability_state", {})
        names = list(self.core) + sorted(META_NAMES)
        for name in capstate.get("loaded", {}):
            cap = self.index.entries.get(name)
            if cap:
                names.extend(cap.tools)
        return [self.registry.get(n) for n in dict.fromkeys(names) if n in self.registry.names()]

    def prepare(self, state: AgentState) -> dict[str, Any]:
        data = state.get("capability_state", {})
        loaded = dict(data.get("loaded", {}))
        step = state["steps"]
        loaded = {name: last for name, last in loaded.items() if step - last < self.idle_rounds}
        return {**data, "loaded": loaded, "disclosed": list(data.get("disclosed", [])), "step": step}

    async def record(self, state: AgentState) -> None:
        registry = self.index.registry
        if not registry:
            return
        names = [t.spec.name for t in self.tools_for(state)]
        from drugpaper.capabilities.tokens import estimate_tokens

        items = [
            {
                "name": name,
                "level": "L2",
                "tokens": estimate_tokens(str(self.registry.get(name).spec.to_schema().parameters)),
            }
            for name in names
        ]
        if state["steps"] == 0:
            items.extend(state.get("capability_state", {}).get("prompt_disclosed", []))
        async with registry.db.session(state["ctx"].get("user_id", "default")) as s:
            s.add(
                CapabilityDisclosureRow(
                    id=new_id("disc"),
                    run_id=state["run_id"],
                    audience=self.audience,
                    disclosed=items,
                    used=[c["name"] for c in state["pending_calls"] if c["name"] in names],
                    total_tokens=sum(item["tokens"] for item in items),
                    context={
                        "step": state["steps"],
                        "loaded": list(state.get("capability_state", {}).get("loaded", {})),
                    },
                )
            )


class ArtifactInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    artifact_id: str
    path: list[str | int] = Field(default_factory=list)


class SkillInvocation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    target_ref: str
    inputs: dict[str, Any] = Field(default_factory=dict)
    artifact_inputs: dict[str, ArtifactInput] = Field(default_factory=dict)


class SkillTool(Tool):
    is_skill = True

    def __init__(self, skill: Any, runtime: Any, context_factory: Any) -> None:
        import copy

        from drugpaper.tools.protocol import ToolSpec

        parameters = SkillInvocation.model_json_schema()
        inputs = copy.deepcopy(skill.input_schema)
        input_fields = list(inputs.get("properties", {}))
        inputs["description"] = (
            "Skill从target_ref自动加载当前图像与检测/识别材料；不要复制detections或识别SMILES。"
            + ("可填写字段：" + ", ".join(input_fields) if input_fields else "没有声明输入字段时传inputs={}")
        )
        # Package refs are rooted at #/$defs; lift definitions to the envelope.
        parameters.setdefault("$defs", {}).update(inputs.pop("$defs", {}))
        if inputs.get("required"):
            inputs["description"] = (
                "Skill输入；required字段也可通过artifact_inputs提供，服务端读取完整持久化产物。"
            )
            inputs.pop("required")
        parameters["properties"]["inputs"] = inputs
        if input_fields:
            parameters["properties"]["artifact_inputs"] = {
                "type": "object",
                "additionalProperties": False,
                "properties": {key: {"$ref": "#/$defs/ArtifactInput"} for key in input_fields},
                "description": "已声明输入字段→当前目标产物ID和可选路径；避免复制大JSON。",
            }
        else:
            parameters["properties"].pop("artifact_inputs")
        self.spec = ToolSpec(
            name=skill.name,
            description=skill.l0,
            input_model=SkillInvocation,
            parameters_schema=parameters,
            timeout_s=skill.manifest.budgets.max_wall_time_s + 5,
        )
        self.runtime, self.context_factory = runtime, context_factory
        self.skill = skill

    async def run(self, ctx: ToolContext, args: BaseModel) -> ToolResult:
        request = SkillInvocation.model_validate(args)
        skill_ctx = await self.context_factory(ctx, request.target_ref)
        if (
            self.skill.name in {"molecule_recognize", "som_render"}
            and skill_ctx.materials
            and skill_ctx.materials.target.get("type") != "molecule"
            and "available_artifact_types" in skill_ctx.extras
            and "molecule_boxes" not in skill_ctx.extras["available_artifact_types"]
        ):
            raise ValueError("当前目标尚无检测产物，请先单独调用molecule_detect，再识别或生成编号图")
        import copy

        from drugpaper.persistence.models import ArtifactRow

        inputs = copy.deepcopy(request.inputs)
        for key, link in request.artifact_inputs.items():
            if key in inputs:
                raise ValueError(f"输入字段重复：{key}")
            async with self.runtime.db.session(ctx.user_id) as session:
                row = await session.get(ArtifactRow, link.artifact_id)
                if (
                    row is None
                    or row.user_id != ctx.user_id
                    or row.document_id != skill_ctx.document_id
                    or row.target_ref != request.target_ref
                    or not row.is_active
                    or row.status not in {"completed", "needs_review"}
                ):
                    raise PermissionError("产物不存在、已失效或不属于当前用户和目标")
                value: Any = copy.deepcopy(row.payload)
                if self.skill.name == "mol_backfill" and key == "extraction_payload":
                    visual_types = {
                        s.manifest.produces.artifact_type
                        for s in await self.runtime.registry.list_visible()
                        if s.manifest.vision_structuring
                    }
                    if row.artifact_type not in visual_types:
                        raise ValueError(
                            "extraction_payload必须引用图类结构化Skill的ID-only抽取产物，"
                            "ocsr_result是识别候选，不能代替视觉抽取；请先调用对应图类Skill"
                        )
                for part in link.path:
                    if (
                        isinstance(value, dict)
                        and isinstance(part, str)
                        and part in value
                        or isinstance(value, list)
                        and type(part) is int
                        and 0 <= part < len(value)
                    ):
                        value = value[part] if isinstance(value, dict) else value[int(part)]
                    else:
                        raise ValueError(f"产物路径不存在：{link.path}")
                inputs[key] = value
        if self.skill.manifest.vision_structuring:
            prepare = skill_ctx.extras.get("prepare_vision")
            if prepare:
                await prepare(skill_ctx)
        outcome = await self.runtime.run(self.spec.name, request.target_ref, inputs, skill_ctx)
        artifacts = [a.model_dump(mode="json") for a in outcome.artifacts]
        needs_review = outcome.status != "completed" or any(
            a.status != "completed" for a in outcome.artifacts
        )
        data: dict[str, Any] = {
            "cached": outcome.cached,
            "status": outcome.status,
            "needs_review": needs_review,
            "artifacts": artifacts,
        }
        # Keep stable handoff IDs visible even when payloads exceed model context.
        import orjson

        if len(orjson.dumps(data).decode()) > self.spec.max_output_chars:
            data["artifacts"] = [
                {k: a[k] for k in ("artifact_id", "artifact_type", "target_ref", "status")} for a in artifacts
            ]
            data["payload_omitted"] = True
            data["next_step"] = "通过artifact_inputs引用完整产物；执行完成不等于内容验收通过。"
        return ToolResult(
            ok=outcome.status == "completed",
            error=outcome.error,
            data=data,
            refs=[request.target_ref] if outcome.artifacts else [],
        )
