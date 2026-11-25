from __future__ import annotations

import asyncio
import base64
import copy
import fcntl
import os
import sys
import time
from pathlib import Path
from typing import Any

from drugpaper.agents.figure.accounting import CallAccounting
from drugpaper.agents.figure.graph import FigureAgent
from drugpaper.api.monitoring import _BEARER, _SECRET
from drugpaper.config.settings import Settings
from drugpaper.facts.figure_result import persist_result
from drugpaper.facts.flowchart_delivery import compact_result
from drugpaper.gateway.openai_compat import OpenAICompatClient
from drugpaper.persistence.models import Document, FigureReportRow, RunRow, utcnow
from drugpaper.services import Services
from drugpaper.skills.protocol import ToolFn
from drugpaper.tools.protocol import ToolContext
from drugpaper.workbench.store import STAGES, figure_dir, materials_stamp, now, read, write


def clean(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): "[redacted]" if _SECRET.search(str(k)) else clean(v) for k, v in value.items()}
    if isinstance(value, list):
        return [clean(v) for v in value]
    return _BEARER.sub("[redacted]", value) if isinstance(value, str) else value


class Recorder:
    def __init__(self, directory: Path, sv: Services):
        self.directory, self.sv = directory, sv
        self.stage = ""
        self.index = 0

    async def event(self, kind: str, data: Any) -> None:
        self.index += 1
        index = self.index

        async def assets(value: Any) -> Any:
            if isinstance(value, dict):
                result: dict[str, Any] = {}
                for k, v in value.items():
                    if k == "data_b64" and v:
                        result["blob_ref"] = await self.sv.store.put(base64.b64decode(v), "image/png")
                    elif k == "images_b64" and isinstance(v, list):
                        result["image_refs"] = [
                            await self.sv.store.put(base64.b64decode(x), "image/png") for x in v
                        ]
                    else:
                        result[k] = await assets(v)
                return result
            if isinstance(value, list):
                return [await assets(v) for v in value]
            if isinstance(value, str) and value.startswith("data:image/") and ";base64," in value:
                prefix, raw = value.split(";base64,", 1)
                return {
                    "blob_ref": await self.sv.store.put(base64.b64decode(raw), prefix[5:]),
                    "mime": prefix[5:],
                }
            return value

        write(
            self.directory / "events" / f"{index:06d}.json",
            clean({"stage": self.stage, "kind": kind, "at": now(), "data": await assets(data)}),
        )


class RecordingClient:
    def __init__(self, spec: Any, recorder: Recorder):
        self.client = OpenAICompatClient(spec, event_sink=recorder.event)
        self.recorder = recorder
        self.model = spec.model_name

    async def chat(self, req: Any, *, task: str = "default") -> Any:
        data = req.model_dump(exclude={"response_schema"})
        if req.response_schema:
            data["response_schema"] = req.response_schema.model_json_schema()
        await self.recorder.event("model_request", {"task": task, "model": self.model, "request": data})
        try:
            response = await self.client.chat(req, task=task)
            await self.recorder.event("model_response", response.model_dump(mode="json"))
            return response
        except Exception as exc:
            await self.recorder.event("model_error", {"error": str(exc), "raw": getattr(exc, "raw", None)})
            raise

    async def aclose(self) -> None:
        await self.client.aclose()


class DebugAgent(FigureAgent):
    def __init__(self, sv: Services, recorder: Recorder, code_hash: str):
        super().__init__(sv)
        self.recorder, self.code_hash = recorder, code_hash

    async def _exploration_event(self, state: Any, event: dict[str, Any]) -> None:
        await super()._exploration_event(state, event)
        await self.recorder.event("agent_exploration", event)

    async def _decorate_context(self, ctx: Any) -> Any:
        ctx.extras["debug_code"] = self.code_hash
        impls: dict[str, ToolFn] = {}
        for name, fn in ctx.tools.impls.items():

            async def wrapped(_fn: Any = fn, _name: str = name, **kwargs: Any) -> Any:
                await self.recorder.event("tool_request", {"name": _name, "arguments": kwargs})
                try:
                    result = await _fn(**kwargs)
                    await self.recorder.event("tool_response", {"name": _name, "result": result})
                    return result
                except Exception as exc:
                    await self.recorder.event("tool_error", {"name": _name, "error": str(exc)})
                    raise

            impls[name] = wrapped
        ctx.tools.impls = impls
        return ctx

    async def _run_skill(self, state: Any, name: str, *args: Any, **kwargs: Any) -> Any:
        bundle, ctx = await self._ctx(state)
        assert ctx.materials is not None
        ref = kwargs.get("ref") or state["node_ref"]
        if ref != state["node_ref"]:
            ctx = await bundle.context_for(
                ToolContext(
                    user_id=state["user_id"],
                    document_id=state["document_id"],
                    run_id=state["run_id"],
                    trace_id=state["trace_id"],
                ),
                ref,
            )
            assert ctx.materials is not None
        images = kwargs.get("images")
        if images is None:
            images = ctx.materials.images
        target = ctx.materials.target
        geometry = target.get("geometry", {})
        molecule = (
            {
                "node_ref": ref,
                "mol_id": target.get("attributes", {}).get("reading_index"),
                "local_bbox": geometry.get("local_bbox"),
                "parent_region_index": geometry.get("parent_region_index"),
                "regions": geometry.get("regions", []),
            }
            if target.get("type") == "molecule"
            else None
        )
        skill = await bundle.registry.resolve(name)
        await self.recorder.event(
            "skill_start",
            {
                "name": name,
                "version": skill.version,
                "hash": skill.content_hash,
                "target": ref,
                **({"molecule": molecule} if molecule else {}),
                "inputs": args[0] if args else kwargs.get("inputs", {}),
                "images": [
                    {
                        **image.model_dump(),
                        "role": "review_input"
                        if name == "figure_self_check" and kwargs.get("images") is not None
                        else "numbered_target"
                        if kwargs.get("images") is not None and name != "molecule_recognize"
                        else "molecule_crop"
                        if molecule
                        else "target",
                    }
                    for image in images
                ],
                "reference_images": [
                    {**image.model_dump(), "role": "original_reference"}
                    for image in (kwargs.get("reference_images") or [])
                ],
            },
        )
        result = await super()._run_skill(state, name, *args, **kwargs)
        await self.recorder.event(
            "skill_result", {"name": name, "payload": result, "trace": state["trace"][-1]}
        )
        return result


async def execute(directory: Path) -> None:
    meta = read(directory / "run.json")
    settings = Settings(_env_file=None, **read(directory / "settings.json"))
    sv = Services.from_settings(settings)
    recorder = Recorder(directory, sv)
    recorder.index = len(list((directory / "events").glob("*.json")))
    agent = DebugAgent(sv, recorder, meta["code_hash"])
    root = directory.parent.parent
    fdir = figure_dir(root, meta["node_ref"])
    fdir.mkdir(parents=True, exist_ok=True)
    lease = (fdir / "execution.lock").open("a")
    slot = None
    try:
        fcntl.flock(lease, fcntl.LOCK_EX | fcntl.LOCK_NB)
        # Bound all subprocess runs sharing this data directory before loading models.
        slots = root.parent / "slots"
        slots.mkdir(parents=True, exist_ok=True)
        while slot is None:
            if read(directory / "control.json", {}).get("pause"):
                meta.update(status="paused", next_stage=meta["start"])
                return
            for i in range(min(settings.figure_concurrency, 3 if sv.db.is_postgres else 1)):
                candidate = (slots / f"{i}.lock").open("a")
                try:
                    fcntl.flock(candidate, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    slot = candidate
                    break
                except BlockingIOError:
                    candidate.close()
            if slot is None:
                await asyncio.sleep(0.25)
        async with sv.db.session() as session:
            doc = await session.get(Document, meta["document_id"])
            if not doc or doc.deleted_at or doc.active_revision != meta["revision"]:
                raise ValueError("文档不存在或revision已改变")
        bundle = await sv.skills()
        bundle.models._factory = lambda spec: RecordingClient(spec, recorder)
        state: Any = copy.deepcopy(
            meta.get("initial_state")
            or {
                "document_id": meta["document_id"],
                "node_ref": meta["node_ref"],
                "user_id": "default",
                "blackboard": {},
                "mode": "full",
                "round": 0,
                "accounting": CallAccounting().model_dump(),
                "artifact_ids": [],
                "trace": [],
                "force": False,
            }
        )
        state.update(run_id=meta["id"], trace_id=meta["id"], force=meta.get("force", False))
        # New run accounting includes only its own calls; inherited state stays linked in meta.
        state["accounting"] = CallAccounting().model_dump()
        meta.update(status="running", pid=os.getpid(), started_at=now())
        write(directory / "run.json", meta)
        async with sv.db.session() as session:
            session.add(
                RunRow(
                    id=meta["id"],
                    document_id=meta["document_id"],
                    user_id="default",
                    run_type="workbench",
                    component="figure_debug",
                    status="running",
                    trace_id=meta["id"],
                    inputs={
                        "node_ref": meta["node_ref"],
                        "source_run": meta.get("source_run"),
                        "start": meta["start"],
                    },
                )
            )
        controls = read(fdir / "corrections.json", {})
        for stage in STAGES[STAGES.index(meta["start"]) :]:
            command = read(directory / "control.json", {})
            if command.get("pause") or stage == meta.get("pause_before"):
                meta.update(status="paused", next_stage=stage)
                break
            recorder.stage = stage
            started = time.perf_counter()
            step: dict[str, Any] = {
                "stage": stage,
                "status": "running",
                "started_at": now(),
                "source_run": None,
            }
            meta["steps"] = [s for s in meta.get("steps", []) if s["stage"] != stage] + [step]
            write(directory / "run.json", meta)
            await recorder.event("stage_start", {"stage": stage})
            if stage == "classify":
                await agent._classify(state)
            elif stage == "plan":
                if controls.get("image_type"):
                    state["classification"] = {
                        **state["classification"],
                        "image_type": controls["image_type"],
                        "manual": True,
                    }
                await agent._plan(state)
                chosen = meta.get("skill")
                if chosen:
                    spec = await bundle.registry.resolve(chosen)
                    if not spec.manifest or not spec.manifest.vision_structuring:
                        raise ValueError("指定的Skill不是图表结构化Skill")
                    state["chain"] = [
                        chosen
                        if getattr((await bundle.registry.resolve(n)).manifest, "vision_structuring", False)
                        else n
                        for n in state["chain"]
                    ]
            elif stage == "detect":
                _, ctx = await agent._ctx(state)
                assert ctx.materials is not None
                state["image_products"] = []
                for i, image in enumerate(ctx.materials.images):
                    state["image_products"].append(
                        {
                            "stage": "original",
                            "segment": i,
                            "page": ctx.materials.target["regions"][i]["page"],
                            "blob_ref": await sv.store.put(
                                base64.b64decode(image.data_b64 or ""), "image/png"
                            ),
                            "status": "source_only",
                        }
                    )
                if "molecule_detect" in state["chain"]:
                    if meta.get("reuse", {}).get("detect"):
                        await recorder.event(
                            "upstream_reuse", {"stage": "detect", "source_run": meta.get("source_run")}
                        )
                    elif controls.get("boxes"):
                        await recorder.event(
                            "manual_reuse", {"reason": "保留人工分子框，恢复原始结果后可重新检测"}
                        )
                    else:
                        await agent._run_skill(state, "molecule_detect")
            elif stage == "recognize":
                if meta.get("reuse", {}).get("recognize"):
                    await recorder.event(
                        "upstream_reuse", {"stage": "recognize", "source_run": meta.get("source_run")}
                    )
                elif "molecule_recognize" in state["chain"]:
                    _, ctx = await agent._ctx(state)
                    assert ctx.materials is not None
                    for child in ctx.materials.target.get("children", []):
                        if meta.get("operation") == "recognize_one" and child["ref"] != meta.get(
                            "molecule_ref"
                        ):
                            continue
                        manual_id = controls.get("smiles", {}).get(child["ref"])
                        if child["status"] != "removed" and (
                            not manual_id or meta.get("operation") == "recognize_one"
                        ):
                            await agent._run_skill(
                                state,
                                "molecule_recognize",
                                ref=child["ref"],
                                force=meta.get("operation") == "recognize_one" or None,
                            )
                            if manual_id:
                                from drugpaper.persistence.models import NodeRow

                                async with sv.db.session() as session:
                                    row = await session.get(
                                        NodeRow, (meta["document_id"], meta["revision"], child["local_id"])
                                    )
                                    if row:
                                        row.attributes = {**row.attributes, "structure_id": manual_id}
                                        row.status = "active"
                                await recorder.event(
                                    "manual_reuse",
                                    {
                                        "node_ref": child["ref"],
                                        "reason": "重识别结果已记录，默认仍采用人工SMILES",
                                    },
                                )
                        elif child["status"] != "removed" and manual_id:
                            saved = next(
                                (
                                    item
                                    for item in ctx.materials.target.get("structures", [])
                                    if item.get("node_ref") == child["ref"]
                                ),
                                None,
                            )
                            await recorder.event(
                                "manual_reuse",
                                {
                                    "node_ref": child["ref"],
                                    "mol_id": child.get("attributes", {}).get("reading_index"),
                                    "reason": "沿用人工确认的分子结构，本轮未调用识别工具",
                                    "structure": saved,
                                },
                            )
            elif stage == "som":
                _, ctx = await agent._ctx(state)
                assert ctx.materials is not None
                for name in ("som_render", "label_bind"):
                    if name in state["chain"]:
                        result = await agent._run_skill(state, name)
                        if name == "som_render":
                            for image in result.get("images", []):
                                state["image_products"].append(
                                    {
                                        "stage": "model_input_numbered",
                                        "segment": image["parent_region_index"],
                                        "page": ctx.materials.target["regions"][image["parent_region_index"]][
                                            "page"
                                        ],
                                        "blob_ref": image["blob_ref"],
                                        "status": "input_only",
                                    }
                                )
            else:
                if stage == "structure":
                    state["material"] = await agent._material(state)
                await getattr(agent, "_" + stage)(state)
            if stage in {"post", "report", "deliver"} and state.get("backfilled_extraction"):
                source = state["report"] if stage != "post" else state
                metadata, result = await persist_result(
                    sv.store_for("default"), source, "backfilled" if stage == "post" else "reported"
                )
                write(directory / "final-result.json", result, indent=2)
                if stage != "post":
                    write(directory / "delivery-result.json", compact_result(result), indent=2)
                meta["result_file"] = metadata
                state["result_file"] = metadata
                if stage != "post":
                    state["report"]["result_file"] = metadata
            if stage == "deliver" and state.get("summary_result"):
                write(directory / "summary-result.json", state["summary_result"], indent=2)
            step.update(
                status="completed", ended_at=now(), duration_ms=round((time.perf_counter() - started) * 1000)
            )
            write(directory / "states" / f"{stage}.json", state)
            meta["materials_stamp"] = await materials_stamp(
                sv, meta["document_id"], meta["revision"], meta["node_ref"]
            )
            await recorder.event(
                "stage_result",
                {
                    "stage": stage,
                    "classification": state.get("classification"),
                    "chain": state.get("chain"),
                    "accounting": state.get("accounting"),
                    **({"summary_result": state.get("summary_result")} if stage == "deliver" else {}),
                },
            )
            meta["next_stage"] = STAGES[STAGES.index(stage) + 1] if stage != STAGES[-1] else None
            write(directory / "run.json", meta)
            if stage == meta.get("stop_after") and stage != STAGES[-1]:
                meta["status"] = "completed" if meta.get("operation", "parse") != "parse" else "paused"
                if meta["status"] == "completed":
                    meta["next_stage"] = None
                break
        else:
            meta["status"] = state.get("report", {}).get("status", "completed")
            if state.get("report") and meta["start"] != "deliver":
                report = state["report"]
                async with sv.db.session() as session:
                    session.add(
                        FigureReportRow(
                            id=report["report_id"],
                            user_id="default",
                            document_id=meta["document_id"],
                            node_ref=meta["node_ref"],
                            mode="full",
                            input_fingerprint=meta["id"],
                            status=report["status"],
                            payload=report,
                        )
                    )
        meta["ended_at"] = now()
    except Exception as exc:
        meta.update(status="failed", error=clean(f"{type(exc).__name__}: {exc}"), ended_at=now())
        for step in meta.get("steps", []):
            if step["status"] == "running":
                step.update(status="failed", error=meta["error"])
    finally:
        write(directory / "run.json", meta)
        try:
            async with sv.db.session() as session:
                run_row = await session.get(RunRow, meta["id"])
                if run_row:
                    run_row.status = meta["status"]
                    run_row.ended_at = utcnow()
                    run_row.outputs = {"next_stage": meta.get("next_stage"), "steps": meta.get("steps", [])}
                    run_row.metrics = locals().get("state", {}).get("accounting", {})
                    run_row.error = {"message": meta["error"]} if meta.get("error") else None
        finally:
            if slot:
                slot.close()
        lease.close()
        await sv.aclose()


if __name__ == "__main__":
    asyncio.run(execute(Path(sys.argv[1]).resolve()))
