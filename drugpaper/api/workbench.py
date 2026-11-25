"""Development-only figure workbench. Durable files, isolated code processes, existing DB facts."""

from __future__ import annotations

import asyncio
import copy
import fcntl
import json
import os
import re
import shutil
import subprocess
import sys
from collections import Counter
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy import select, update
from sqlalchemy.engine import make_url

from drugpaper.api.deps import ServicesDep, SessionDep, require_ir
from drugpaper.api.figure_results import result_response
from drugpaper.api.monitoring import visible
from drugpaper.domain.geometry import BBox, Geometry, Region, local_to_page
from drugpaper.domain.ids import new_id
from drugpaper.facts.compounds import StructureCandidate
from drugpaper.facts.figure_result import persist_result
from drugpaper.facts.flowchart_delivery import compact_result
from drugpaper.persistence.models import NodeRow, StructureCandidateRow
from drugpaper.persistence.repos.artifacts import create_version
from drugpaper.persistence.repos.nodes import get_node, load_ir
from drugpaper.workbench import snapshot
from drugpaper.workbench.reuse import visual_extraction_only
from drugpaper.workbench.runner import clean
from drugpaper.workbench.store import (
    LABELS,
    ROOT,
    STAGES,
    alive,
    digest,
    editable,
    figure_dir,
    materials_stamp,
    now,
    read,
    root_for,
    safe_path,
    write,
)

router = APIRouter(tags=["workbench"])


def reap(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is None:
        asyncio.get_running_loop().call_later(0.5, reap, process)


def run_dir(root: Path, run_id: str) -> Path:
    from drugpaper.workbench.store import run_dir as disk_run_dir

    try:
        return disk_run_dir(root, run_id)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


class Execution(BaseModel):
    node_ref: str
    reuse_molecules: bool = False
    reuse_structure: bool = False
    operation: Literal["parse", "detect", "recognize", "recognize_one"] = "parse"
    molecule_ref: str | None = None
    start: Literal[
        "classify",
        "plan",
        "detect",
        "recognize",
        "som",
        "structure",
        "post",
        "check_l1",
        "check_l2",
        "report",
        "deliver",
    ] = "classify"
    source_run: str | None = None
    stop_after: str | None = None
    pause_before: str | None = None
    skill: str | None = None
    force: bool = False


class FileEdit(BaseModel):
    node_ref: str
    path: str
    content: str = Field(max_length=1_000_000)
    expected_version: int


class Correction(BaseModel):
    node_ref: str
    kind: Literal["type", "box", "smiles", "restore"]
    molecule_ref: str | None = None
    bbox: BBox | None = None
    segment: int = Field(default=0, ge=0)
    deleted: bool = False
    smiles: str | None = None
    image_type: str | None = None
    reason: str | None = Field(default=None, max_length=2000)
    expected_version: int


async def scope(sv: Any, s: Any, docid: str, ref: str | None = None) -> tuple[Any, Path, Any]:
    doc = await visible(s, docid)
    await require_ir(s, docid)
    root = root_for(sv, docid, doc.active_revision)
    node = None
    if ref:
        prefix = f"{docid}/r{doc.active_revision}/"
        if not ref.startswith(prefix):
            raise HTTPException(409, "图表不属于当前文献版本")
        node = await get_node(s, docid, ref[len(prefix) :], doc.active_revision)
        if not node or node.status == "removed" or node.node_type not in {"figure", "table", "scheme"}:
            raise HTTPException(404, "图表不存在")
    return doc, root, node


@contextmanager
def mutation(root: Path) -> Iterator[None]:
    root.mkdir(parents=True, exist_ok=True)
    with (root / "mutation.lock").open("a") as f:
        try:
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise HTTPException(409, "另一操作正在保存，请稍后重试") from exc
        try:
            yield
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)


def runs(root: Path) -> list[dict[str, Any]]:
    result = []
    for p in (root / "runs").glob("*/run.json"):
        item = read(p)
        if item["status"] in {"queued", "running"} and item.get("pid") and not alive(item["pid"]):
            item.update(status="interrupted", error="执行进程已退出，可从已完成步骤创建新运行")
            write(p, item)
        controls = read(figure_dir(root, item["node_ref"]) / "corrections.json", {"version": 0})
        draft = read(figure_dir(root, item["node_ref"]) / "draft.json", {"version": 0, "files": {}})
        needed = [
            c["stale_from"]
            for c in controls.get("history", [])
            if c["version"] > item.get("correction_version", 0)
        ]
        needed += [
            required_stage(p)
            for p, text in draft["files"].items()
            if digest(text) != item.get("manifest", {}).get(p)
        ]
        if needed:
            item["stale_from"] = min(needed, key=STAGES.index)
        result.append(item)
    return sorted(result, key=lambda r: r["created_at"], reverse=True)


def idle(root: Path, ref: str) -> None:
    if any(r["node_ref"] == ref and r["status"] in {"queued", "running"} for r in runs(root)):
        raise HTTPException(409, "此图表正在执行，请暂停并等待阶段结束后操作")


def required_stage(path: str, *, before: Path | None = None, after: Path | None = None) -> str:
    if before is not None and after is not None and visual_extraction_only(path, before, after):
        return "structure"
    if path in {
        "drugpaper/agents/figure/exploration.py",
        "drugpaper/agents/figure/assembly_tools.py",
        "drugpaper/agents/figure/reference_assignments.py",
        "drugpaper/agents/figure/member_normalization.py",
        "drugpaper/skills/output_integrity.py",
    }:
        return "structure"
    if path in {
        "drugpaper/chem/product_candidates.py",
        "drugpaper/chem/instance_delivery.py",
        "drugpaper/chem/assembly.py",
        "drugpaper/chem/assembly_inputs.py",
        "drugpaper/chem/assembly_compat.py",
    }:
        return "post"
    if path in {"prompts/common/mol_instances.md", "prompts/common/member_assignment_fields.md"}:
        return "structure"
    mapping = {
        "mol_backfill": "post",
        "visual_classify": "classify",
        "visual_reclassify": "classify",
        "molecule_detect": "detect",
        "molecule_recognize": "recognize",
        "som_render": "som",
        "label_bind": "som",
        "figure_self_check": "check_l2",
    }
    if path.startswith("drugpaper/skills/builtin/"):
        name = path.split("/")[3]
        return mapping.get(name, "structure")
    return "plan" if path == "config/routing.yaml" else "classify"


def model_result_config(stamp: dict[str, Any]) -> dict[str, Any]:
    """Transport retry policy changes do not invalidate completed upstream results."""
    transport = {
        "timeout_s",
        "max_attempts",
        "retry_base_delay_s",
        "retry_max_delay_s",
        "retry_jitter_s",
        "retry_timeout_multiplier",
        "retry_timeout_max_s",
    }
    return {
        **stamp,
        "candidates": [
            {key: value for key, value in candidate.items() if key not in transport}
            for candidate in stamp.get("candidates", [])
        ],
    }


async def launch(sv: Any, s: Any, docid: str, body: Execution) -> dict[str, Any]:
    doc, root, _ = await scope(sv, s, docid, body.node_ref)
    if body.reuse_molecules and not body.source_run:
        raise HTTPException(422, "复用预处理需要来源运行")
    if body.reuse_structure and (
        body.start != "structure" or body.operation != "parse" or not body.source_run or body.reuse_molecules
    ):
        raise HTTPException(422, "继续结构修正需要来源运行，且从结构化阶段开始")
    if body.operation != "parse":
        body.start = "detect" if body.operation == "detect" else "recognize"
        body.stop_after = body.start
        if body.operation == "recognize_one":
            child = await get_node(
                s, docid, (body.molecule_ref or "").rsplit("/", 1)[-1], doc.active_revision
            )
            if (
                not child
                or child.node_type != "molecule"
                or child.status == "removed"
                or child.parent_id != body.node_ref.rsplit("/", 1)[-1]
                or child.ref != body.molecule_ref
            ):
                raise HTTPException(422, "请选择此图表的有效分子")
    for v in (body.stop_after, body.pause_before):
        if v and v not in STAGES:
            raise HTTPException(422, "未知暂停步骤")
    with mutation(root):
        idle(root, body.node_ref)
        rid = new_id("wd")
        directory = run_dir(root, rid)
        draft = read(figure_dir(root, body.node_ref) / "draft.json", {"version": 0, "files": {}})
        corrections = read(figure_dir(root, body.node_ref) / "corrections.json", {"version": 0})
        manifest = await snapshot.create(sv, directory / "workspace", draft["files"])
        try:
            errors = await asyncio.to_thread(snapshot.validate, directory / "workspace")
        except Exception as exc:
            shutil.rmtree(directory)
            raise HTTPException(422, str(exc)) from exc
        if errors:
            shutil.rmtree(directory)
            raise HTTPException(422, {"message": "局部Skill校验失败", "errors": errors})
        state = None
        inherited = []
        model_stamps = {
            task: (await sv.skills()).models.router.public_config(task)
            for task in (
                "visual_classify",
                "figure_structure",
                "figure_structure_review",
                "figure_reference_review",
                "figure_self_check",
                "figure_explore",
            )
        }
        if body.operation != "parse":
            state = {
                "document_id": docid,
                "node_ref": body.node_ref,
                "user_id": "default",
                "blackboard": {},
                "mode": "full",
                "round": 0,
                "accounting": {},
                "artifact_ids": [],
                "trace": [],
                "force": False,
                "chain": ["molecule_detect", "molecule_recognize", "som_render"],
                "image_products": [],
            }
        elif body.start != "classify":
            if not body.source_run:
                raise HTTPException(422, "从中间步骤开始需要来源运行")
            srcdir = run_dir(root, body.source_run)
            source = read(srcdir / "run.json")
            if not source or source["node_ref"] != body.node_ref:
                raise HTTPException(404, "来源运行不存在或属于其他图表")
            previous = STAGES[STAGES.index(body.start) - 1]
            state = read(srcdir / "states" / f"{previous}.json")
            if not state:
                raise HTTPException(409, f"上游步骤 {previous} 尚未完成")
            if body.start == "deliver" and (
                not state.get("report", {}).get("backfilled_extraction")
                or body.reuse_structure
                or body.reuse_molecules
                or body.skill
            ):
                raise HTTPException(422, "精简步骤仅整理指定的既有报告，不能修改Skill或重新解析")
            changed = [
                p
                for p in set(manifest) | set(source["manifest"])
                if manifest.get(p) != source["manifest"].get(p)
            ]
            needed = [
                required_stage(p, before=srcdir / "workspace" / p, after=directory / "workspace" / p)
                for p in changed
            ]
            needed += [
                stage
                for task, stage in [
                    ("visual_classify", "classify"),
                    ("figure_structure", "structure"),
                    ("figure_structure_review", "structure"),
                    ("figure_reference_review", "structure"),
                    ("figure_explore", "structure"),
                    ("figure_self_check", "check_l2"),
                ]
                if model_result_config(source.get("model_stamps", {}).get(task, model_stamps[task]))
                != model_result_config(model_stamps[task])
            ]
            needed += [
                c["stale_from"]
                for c in corrections.get("history", [])
                if c["version"] > source.get("correction_version", 0)
            ]
            # A deterministic export of an explicitly selected historical report
            # does not claim upstream revalidation under the current code/config.
            if (
                body.start != "deliver"
                and needed
                and STAGES.index(body.start) > min(STAGES.index(x) for x in needed)
            ):
                raise HTTPException(
                    409,
                    {"message": "上游已变化，请从更早步骤重跑", "start_from": min(needed, key=STAGES.index)},
                )
            if (
                body.skill
                and body.skill != source.get("skill")
                and STAGES.index(body.start) > STAGES.index("plan")
            ):
                raise HTTPException(409, "指定Skill发生变化，请从路由步骤重跑")
            for stage in STAGES[: STAGES.index(body.start)]:
                saved = srcdir / "states" / f"{stage}.json"
                if saved.exists():
                    target = directory / "states" / saved.name
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(saved, target)
                inherited.append({"stage": stage, "status": "reused", "source_run": body.source_run})
            if body.reuse_structure:
                completed = read(srcdir / "states/structure.json")
                if (
                    not completed
                    or not completed.get("extraction")
                    or "scheme_extract" not in completed.get("chain", [])
                ):
                    raise HTTPException(409, "来源没有可继续修正的合成路线结构化结果")
                from jsonschema import Draft202012Validator

                from drugpaper.facts.figure_models import VisualExtraction
                from drugpaper.skills.loader import load_package

                package = load_package(
                    directory / "workspace/drugpaper/skills/builtin/scheme_extract",
                    prompts_root=directory / "workspace/prompts",
                )
                try:
                    Draft202012Validator(package.output_schema).validate(completed["extraction"])
                    VisualExtraction.model_validate(completed["extraction"])
                except Exception as exc:
                    raise HTTPException(409, "现有结构与当前模板不兼容，需要首次重新提取") from exc
                state["extraction"] = completed["extraction"]
                state["exploration"] = completed.get("exploration", {})
                state["resume_structure"] = True
        reuse = {}
        if body.reuse_molecules:
            source = read(run_dir(root, body.source_run or "invalid") / "run.json")
            if (
                not source
                or source["node_ref"] != body.node_ref
                or source.get("materials_stamp")
                != await materials_stamp(sv, docid, doc.active_revision, body.node_ref)
            ):
                raise HTTPException(409, "预处理材料已改变，请选择当前有效的来源运行")
            changed = [
                p
                for p in set(manifest) | set(source["manifest"])
                if manifest.get(p) != source["manifest"].get(p)
            ]
            if any(
                STAGES.index(
                    required_stage(
                        p,
                        before=run_dir(root, body.source_run or "invalid") / "workspace" / p,
                        after=directory / "workspace" / p,
                    )
                )
                <= STAGES.index("recognize")
                for p in changed
            ):
                raise HTTPException(409, "检测识别相关代码已改变，请重新预处理")
            assert body.source_run is not None
            srcdir = run_dir(root, body.source_run)
            reuse = {
                "detect": (srcdir / "states/detect.json").exists(),
                "recognize": (srcdir / "states/recognize.json").exists(),
            }
            if source.get("operation") == "recognize_one":
                reuse["recognize"] = False
        elif (
            body.operation == "parse"
            and body.start not in {"classify", "deliver"}
            and source.get("materials_stamp")
            and source["materials_stamp"]
            != await materials_stamp(sv, docid, doc.active_revision, body.node_ref)
            and corrections["version"] == source.get("correction_version", 0)
        ):
            raise HTTPException(409, "当前图表材料已被其他运行修改，请选择最新来源或重新解析")
        meta = {
            "id": rid,
            "document_id": docid,
            "revision": doc.active_revision,
            **body.model_dump(),
            "status": "queued",
            "created_at": now(),
            "steps": inherited,
            "manifest": manifest,
            "model_stamps": model_stamps,
            "draft_version": draft["version"],
            "correction_version": corrections["version"],
            "code_hash": digest(
                {p: v for p, v in manifest.items() if not p.startswith("drugpaper/skills/builtin/")}
            ),
            "initial_state": state,
            "reuse": reuse,
        }
        write(directory / "run.json", meta)
        settings = sv.settings.model_dump(mode="json")
        for key in ["blob_root", "runs_dir", "skills_root"]:
            settings[key] = await asyncio.to_thread(os.path.abspath, settings[key])
        url = make_url(settings["database_url"])
        if url.drivername.startswith("sqlite") and url.database and url.database != ":memory:":
            settings["database_url"] = str(
                url.set(database=await asyncio.to_thread(os.path.abspath, url.database))
            )
        settings.update(
            models_config_path=str(directory / "workspace/config/models.yaml"),
            routing_config_path=str(directory / "workspace/config/routing.yaml"),
            skills_watch_enabled=False,
        )
        write(directory / "settings.json", settings)
        env = {
            k: v for k, v in os.environ.items() if k.lower() not in {"http_proxy", "https_proxy", "all_proxy"}
        }
        # The parent router has resolved private .env values; the isolated cwd has no .env.
        # Pass these only to the child environment, never the public run metadata.
        env.update((await sv.skills()).models.router.env)
        env = {k: v for k, v in env.items() if k.lower() not in {"http_proxy", "https_proxy", "all_proxy"}}
        env.update(DRUGPAPER_WORKBENCH_PACKAGES=str(directory / "workspace"))
        with (directory / "process.log").open("ab") as log:
            process = await asyncio.to_thread(
                subprocess.Popen,
                [sys.executable, "-m", "drugpaper.workbench.runner", str(directory)],
                cwd=directory / "workspace",
                env=env,
                stdout=log,
                stderr=log,
                start_new_session=True,
            )
        meta["pid"] = process.pid
        write(directory / "run.json", meta)
        # Reap without holding the HTTP request; child survives API restarts.
        reap(process)
        return {"id": rid, "status": "queued"}


@router.get("/documents/{document_id}/workbench/runs/{run_id}/file")
async def historical_file(sv: ServicesDep, s: SessionDep, document_id: str, run_id: str, path: str) -> Any:
    _, root, _ = await scope(sv, s, document_id)
    directory = run_dir(root, run_id)
    if not (directory / "run.json").exists() or not editable(path):
        raise HTTPException(404, "运行或文件不存在")
    target = safe_path(directory / "workspace", path)
    if not target.is_file():
        raise HTTPException(404, "该运行未使用此文件")
    return {"path": path, "content": target.read_text()}


@router.get("/documents/{document_id}/workbench")
async def overview(sv: ServicesDep, s: SessionDep, document_id: str) -> Any:
    doc, root, _ = await scope(sv, s, document_id)
    ir = await load_ir(s, document_id)
    visual_ids = {n.local_id for n in ir.nodes if n.node_type in {"figure", "table", "scheme"}}

    def has_visual_parent(n: Any) -> bool:
        seen = set()
        while n.parent_id and n.parent_id not in seen:
            seen.add(n.parent_id)
            if n.parent_id in visual_ids:
                return True
            n = next((x for x in ir.nodes if x.local_id == n.parent_id), None)
            if n is None:
                return False
        return False

    figures = [
        n for n in ir.nodes if n.local_id in visual_ids and n.status != "removed" and not has_visual_parent(n)
    ]
    records = runs(root)
    latest = {n.ref: next((r for r in records if r["node_ref"] == n.ref), None) for n in figures}
    return {
        "revision": doc.active_revision,
        "mode": (doc.meta or {}).get("workbench_mode", "manual"),
        "stages": [{"id": a, "label": b} for a, b in zip(STAGES, LABELS, strict=True)],
        "image_types": json.loads(
            (ROOT / "drugpaper/skills/builtin/visual_classify/schemas/output.schema.json").read_text()
        )["properties"]["image_type"]["enum"],
        "figures": [
            {
                "node_ref": n.ref,
                "label": n.label,
                "type": n.node_type,
                "regions": [r.model_dump() for r in n.geometry.regions],
                "latest": {
                    k: v for k, v in (latest[n.ref] or {}).items() if k not in {"initial_state", "manifest"}
                }
                if latest[n.ref]
                else None,
            }
            for n in figures
        ],
        "counts": dict(Counter((latest[n.ref] or {}).get("status", "unprocessed") for n in figures)),
    }


@router.get("/documents/{document_id}/workbench/runs")
async def list_runs(sv: ServicesDep, s: SessionDep, document_id: str, node_ref: str | None = None) -> Any:
    _, root, _ = await scope(sv, s, document_id, node_ref)
    return [
        {k: v for k, v in r.items() if k not in {"initial_state", "manifest"}}
        for r in runs(root)
        if not node_ref or r["node_ref"] == node_ref
    ]


@router.post("/documents/{document_id}/workbench/runs", status_code=202)
async def execute(sv: ServicesDep, s: SessionDep, document_id: str, body: Execution) -> Any:
    return await launch(sv, s, document_id, body)


@router.get("/documents/{document_id}/workbench/runs/{run_id}")
async def run_detail(sv: ServicesDep, s: SessionDep, document_id: str, run_id: str) -> Any:
    _, root, _ = await scope(sv, s, document_id)
    records = runs(root)
    directory = run_dir(root, run_id)
    meta = next((r for r in records if r["id"] == run_id), None)
    if not meta:
        raise HTTPException(404, "运行不存在")
    states = {p.stem: read(p) for p in (directory / "states").glob("*.json")}
    process_error = None
    if meta["status"] == "interrupted" and (directory / "process.log").exists():
        process_error = re.sub(
            r"(://)[^\s/@]+:[^\s/@]+@",
            r"\1[redacted]@",
            (directory / "process.log").read_text(errors="replace")[-20000:],
        )
    return clean(
        {
            "process_error": process_error,
            "run": {k: v for k, v in meta.items() if k != "initial_state"},
            "states": states,
            "events": [read(p) for p in sorted((directory / "events").glob("*.json"))],
        }
    )


@router.get("/documents/{document_id}/workbench/runs/{run_id}/result")
async def run_result(
    sv: ServicesDep,
    s: SessionDep,
    document_id: str,
    run_id: str,
    download: bool = False,
    view: Literal["summary", "delivery", "full"] = "summary",
) -> JSONResponse:
    _, root, _ = await scope(sv, s, document_id)
    record = next((r for r in runs(root) if r["id"] == run_id), None)
    if record is None:
        raise HTTPException(404, "运行不存在")
    directory = run_dir(root, run_id)
    result = read(directory / "final-result.json")
    if result is None:
        if record["status"] in {"queued", "running"}:
            raise HTTPException(409, "运行尚在保存结果，请稍后重试")
        report_state = read(directory / "states/report.json", {})
        post_state = read(directory / "states/post.json", {})
        source = report_state.get("report") or post_state
        if not source.get("backfilled_extraction"):
            raise HTTPException(409, "此运行尚未完成回填，暂无可交付结果")
        _, result = await persist_result(
            sv.store_for("default"), source, "reported" if report_state.get("report") else "backfilled"
        )
        write(directory / "final-result.json", result, indent=2)
    if result.get("processing_stage") == "backfilled":
        return result_response(result, download, view)
    delivery = compact_result(result)
    if read(directory / "delivery-result.json") != delivery:
        write(directory / "delivery-result.json", delivery, indent=2)
    from drugpaper.facts.flowchart_delivery import summarize_result

    summary = summarize_result(result)
    if read(directory / "summary-result.json") != summary:
        write(directory / "summary-result.json", summary, indent=2)
    return result_response(result, download, view)


@router.post("/documents/{document_id}/workbench/runs/{run_id}/pause")
async def pause(sv: ServicesDep, s: SessionDep, document_id: str, run_id: str) -> Any:
    _, root, _ = await scope(sv, s, document_id)
    directory = run_dir(root, run_id)
    if not (directory / "run.json").exists():
        raise HTTPException(404, "运行不存在")
    write(directory / "control.json", {"pause": True})
    return {"status": "pause_requested", "notice": "当前步骤完成后暂停"}


@router.get("/documents/{document_id}/workbench/files")
async def get_files(
    sv: ServicesDep, s: SessionDep, document_id: str, node_ref: str, path: str | None = None
) -> Any:
    _, root, _ = await scope(sv, s, document_id, node_ref)
    draft = read(figure_dir(root, node_ref) / "draft.json", {"version": 0, "files": {}})
    if path:
        if not editable(path):
            raise HTTPException(422, "不可编辑此路径")
        original = safe_path(ROOT, path)
        baseline = original.read_text() if original.is_file() else ""
        if path.startswith("drugpaper/skills/builtin/"):
            parts = Path(path).parts
            try:
                skill = await (await sv.skills()).registry.resolve(parts[3])
                active = safe_path(skill.path, str(Path(*parts[4:])))
                baseline = active.read_text() if active.is_file() else ""
            except KeyError:
                pass
        return {
            "path": path,
            "version": draft["version"],
            "content": draft["files"].get(path, baseline),
            "baseline": baseline,
            "local": path in draft["files"],
        }
    return {
        "version": draft["version"],
        "files": sorted(set(snapshot.files()) | set(draft["files"])),
        "changed": list(draft["files"]),
    }


@router.put("/documents/{document_id}/workbench/files")
async def save_file(sv: ServicesDep, s: SessionDep, document_id: str, body: FileEdit) -> Any:
    _, root, _ = await scope(sv, s, document_id, body.node_ref)
    if not editable(body.path):
        raise HTTPException(422, "不可编辑此路径")
    with mutation(root):
        idle(root, body.node_ref)
        path = figure_dir(root, body.node_ref) / "draft.json"
        draft = read(path, {"version": 0, "files": {}})
        if draft["version"] != body.expected_version:
            raise HTTPException(409, "草稿已更新，请重新读取后保存")
        draft["files"][body.path] = body.content
        draft["version"] += 1
        write(path, draft)
        write(path.parent / "drafts" / f"{draft['version']}.json", draft)
    return {"version": draft["version"], "status": "saved_local"}


@router.post("/documents/{document_id}/workbench/validate")
async def validate_draft(sv: ServicesDep, s: SessionDep, document_id: str, body: Execution) -> Any:
    _, root, _ = await scope(sv, s, document_id, body.node_ref)
    with mutation(root):
        draft = read(figure_dir(root, body.node_ref) / "draft.json", {"files": {}})
        target = root / "validations" / new_id("val")
        await snapshot.create(sv, target, draft["files"])
        try:
            errors = await asyncio.to_thread(snapshot.validate, target)
            return {
                "ok": not errors,
                "errors": errors,
                "notice": "包结构与Python语法校验；实际行为以局部试运行为准",
            }
        except Exception as exc:
            return {"ok": False, "errors": [str(exc)]}
        finally:
            shutil.rmtree(target)


class Publish(BaseModel):
    node_ref: str
    expected_version: int


@router.post("/documents/{document_id}/workbench/publish")
async def publish(sv: ServicesDep, s: SessionDep, document_id: str, body: Publish) -> Any:
    _, root, _ = await scope(sv, s, document_id, body.node_ref)
    with mutation(root):
        idle(root, body.node_ref)
        draft = read(figure_dir(root, body.node_ref) / "draft.json", {"version": 0, "files": {}})
        if draft["version"] != body.expected_version:
            raise HTTPException(409, "草稿版本已改变")
        successful = next(
            (
                r
                for r in runs(root)
                if r["node_ref"] == body.node_ref
                and r["draft_version"] == body.expected_version
                and r["status"] in {"ok", "remediated", "needs_review"}
            ),
            None,
        )
        if not successful:
            raise HTTPException(409, "请先使用当前局部版本完成一次整图试运行，再更新全局")
        # Archive previous source before explicit global publication.
        archive = root / "publications" / new_id("pub")
        original = {
            p: safe_path(ROOT, p).read_text() if safe_path(ROOT, p).is_file() else None
            for p in draft["files"]
        }
        write(archive / "before.json", original)
        for p, text in draft["files"].items():
            target = safe_path(ROOT, p)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text)
        write(archive / "published.json", draft)
        await (await sv.skills()).watcher.reload(publish=True)
    return {
        "status": "published",
        "notice": "全局源码已更新；公共Python工具改动需重启常驻后端进程，已有调试运行保持原快照",
    }


@router.get("/documents/{document_id}/workbench/molecules")
async def molecules(sv: ServicesDep, s: SessionDep, document_id: str, node_ref: str) -> Any:
    doc, root, parent = await scope(sv, s, document_id, node_ref)
    rows = list(
        (
            await s.execute(
                select(NodeRow).where(
                    NodeRow.document_id == document_id,
                    NodeRow.revision == doc.active_revision,
                    NodeRow.parent_local_id == parent.local_id,
                    NodeRow.node_type == "molecule",
                )
            )
        ).scalars()
    )
    structures = list(
        (
            await s.execute(
                select(StructureCandidateRow).where(
                    StructureCandidateRow.document_id == document_id,
                    StructureCandidateRow.user_id == "default",
                    StructureCandidateRow.is_active.is_(True),
                )
            )
        ).scalars()
    )
    controls = read(figure_dir(root, node_ref) / "corrections.json", {"version": 0})
    result = []
    for row in rows:
        ref = f"{document_id}/r{doc.active_revision}/{row.local_id}"
        candidate = next(
            (
                x.payload
                for x in structures
                if x.node_ref == ref and x.id == (row.attributes or {}).get("structure_id")
            ),
            None,
        )
        result.append(
            {
                "node_ref": ref,
                "status": row.status,
                "geometry": row.geometry,
                "attributes": row.attributes,
                "structure": candidate,
                "manual_smiles": ref in controls.get("smiles", {}),
                "manual_box": row.local_id in controls.get("boxes", {}),
            }
        )
    return {
        "version": controls["version"],
        "image_type": controls.get("image_type"),
        "molecules": result,
        "history": controls.get("history", []),
    }


@router.post("/documents/{document_id}/workbench/corrections")
async def correct(sv: ServicesDep, s: SessionDep, document_id: str, body: Correction) -> Any:
    doc, root, parent = await scope(sv, s, document_id, body.node_ref)
    with mutation(root):
        idle(root, body.node_ref)
        file = figure_dir(root, body.node_ref) / "corrections.json"
        controls = read(file, {"version": 0, "boxes": {}, "smiles": {}, "baseline": {}, "history": []})
        if controls["version"] != body.expected_version:
            raise HTTPException(409, "人工修正已更新，请刷新")
        if body.kind == "type":
            allowed = json.loads(
                (ROOT / "drugpaper/skills/builtin/visual_classify/schemas/output.schema.json").read_text()
            )["properties"]["image_type"]["enum"]
            if body.image_type and body.image_type not in allowed:
                raise HTTPException(422, "未知图表类型")
            controls["image_type"] = body.image_type
            stale = "plan"
        else:
            rows = list(
                (
                    await s.execute(
                        select(NodeRow).where(
                            NodeRow.document_id == document_id,
                            NodeRow.revision == doc.active_revision,
                            NodeRow.parent_local_id == parent.local_id,
                            NodeRow.node_type == "molecule",
                        )
                    )
                ).scalars()
            )
            local = (
                body.molecule_ref.rsplit("/", 1)[-1]
                if body.molecule_ref
                else parent.local_id
                + ".m"
                + str(max([int(r.local_id.rsplit(".m", 1)[-1]) for r in rows] or [-1]) + 1).zfill(3)
            )
            row = next((r for r in rows if r.local_id == local), None)
            if body.molecule_ref and (
                body.molecule_ref != f"{document_id}/r{doc.active_revision}/{local}" or row is None
            ):
                raise HTTPException(404, "分子不属于选中图表")
            if local not in controls["baseline"]:
                controls["baseline"][local] = (
                    None
                    if not row
                    else {
                        "geometry": copy.deepcopy(row.geometry),
                        "attributes": copy.deepcopy(row.attributes),
                        "status": row.status,
                        "source": row.source,
                    }
                )
            if body.kind == "restore":
                baseline = controls["baseline"].get(local)
                if not row:
                    raise HTTPException(404, "分子不存在")
                if baseline:
                    row.geometry = baseline["geometry"]
                    row.attributes = baseline["attributes"]
                    row.status = baseline["status"]
                    row.source = baseline["source"]
                    sid = row.attributes.get("structure_id")
                    if sid:
                        candidate = await s.get(StructureCandidateRow, sid)
                        if candidate:
                            candidate.is_active = True
                else:
                    row.status = "removed"
                controls["boxes"].pop(local, None)
                controls["smiles"].pop(body.molecule_ref, None)
                stale = "recognize"
            elif body.kind == "box":
                if (
                    not body.bbox
                    or not body.bbox.in_bounds()
                    or body.bbox.area <= 0
                    or body.segment >= len(parent.geometry.regions)
                ):
                    raise HTTPException(422, "分子框必须位于图表分段的0到1坐标内，且具有面积")
                if not row:
                    row = NodeRow(
                        document_id=document_id,
                        revision=doc.active_revision,
                        local_id=local,
                        user_id="default",
                        node_type="molecule",
                        parent_local_id=parent.local_id,
                        attributes={
                            "reading_index": max([r.attributes.get("reading_index", 0) for r in rows] or [-1])
                            + 1
                        },
                    )
                    s.add(row)
                region = parent.geometry.regions[body.segment]
                row.geometry = Geometry(
                    local_bbox=body.bbox,
                    parent_region_index=body.segment,
                    regions=[
                        Region(page=region.page, bbox=local_to_page(body.bbox, region.bbox), derived=True)
                    ],
                    derived=True,
                ).model_dump(mode="json")
                row.page = region.page
                row.status = "removed" if body.deleted else "needs_review"
                row.source = "human"
                row.attributes = {**row.attributes, "structure_id": None, "masked_regions": []}
                controls["boxes"][local] = {
                    "bbox": body.bbox.model_dump(),
                    "segment": body.segment,
                    "deleted": body.deleted,
                }
                controls["smiles"].pop(body.molecule_ref, None)
                ref = f"{document_id}/r{doc.active_revision}/{local}"
                await s.execute(
                    update(StructureCandidateRow)
                    .where(StructureCandidateRow.node_ref == ref, StructureCandidateRow.user_id == "default")
                    .values(is_active=False)
                )
                stale = "recognize"
            else:
                if not row or row.status == "removed" or not body.smiles:
                    raise HTTPException(422, "请选择有效分子并输入SMILES")
                assert body.molecule_ref is not None
                from drugpaper.chem.esmiles import split
                from drugpaper.chem.normalize import canonical

                smiles_core = split(body.smiles)[0]
                normalized = await asyncio.to_thread(canonical, smiles_core)
                if not normalized.valid:
                    raise HTTPException(422, normalized.error or "无效SMILES")
                artifact = await create_version(
                    s,
                    document_id=document_id,
                    artifact_type="manual_structure",
                    target_ref=body.node_ref,
                    work_unit_key="manual:" + body.molecule_ref,
                    payload={"smiles": body.smiles, "source": "manual", "reason": body.reason},
                    user_id="default",
                )
                payload = StructureCandidate(
                    structure_id=new_id("str"),
                    node_ref=body.molecule_ref,
                    source="manual",
                    e_smiles_raw=body.smiles,
                    smiles_core=smiles_core,
                    canonical_smiles=normalized.canonical_smiles,
                    inchikey=normalized.inchikey,
                    formula=None if "*" in body.smiles else normalized.formula,
                    validation={"valid": True},
                    evidence=[{"node_id": body.molecule_ref, "role": "primary"}],
                ).model_dump()
                s.add(
                    StructureCandidateRow(
                        id=payload["structure_id"],
                        user_id="default",
                        document_id=document_id,
                        artifact_id=artifact.id,
                        target_ref=body.node_ref,
                        node_ref=body.molecule_ref,
                        source="manual",
                        payload=payload,
                    )
                )
                row.attributes = {**row.attributes, "structure_id": payload["structure_id"]}
                row.status = "active"
                controls["smiles"][body.molecule_ref] = payload["structure_id"]
                stale = "post"
        controls["version"] += 1
        controls["history"].append(
            {
                "version": controls["version"],
                "reason": body.reason,
                "kind": body.kind,
                "molecule_ref": body.molecule_ref,
                "stale_from": stale,
                "at": now(),
            }
        )
        await s.commit()
        write(file, controls)
        write(file.parent / "corrections" / f"{controls['version']}.json", controls)
        return {
            "version": controls["version"],
            "stale_from": stale,
            "notice": "相关下游需要重跑；历史运行保留",
        }


@router.get("/documents/{document_id}/workbench/layout")
async def layout(sv: ServicesDep, s: SessionDep, document_id: str, page: int = Query(ge=0)) -> Any:
    _, _, _ = await scope(sv, s, document_id)
    ir = await load_ir(s, document_id)
    byid = ir.index()
    visual = {n.local_id for n in ir.nodes if n.node_type in {"figure", "table", "scheme"}}
    selected = []
    for n in ir.nodes:
        if n.status == "removed" or n.node_type in {
            "document",
            "front_matter",
            "section",
            "discarded",
            "molecule",
            "figure_part",
            "chart_panel",
        }:
            continue
        ancestor = n.parent_id
        hidden = False
        seen = set()
        while ancestor and ancestor not in seen:
            seen.add(ancestor)
            if ancestor in visual:
                hidden = True
                break
            ancestor = byid[ancestor].parent_id if ancestor in byid else None
        if hidden:
            continue
        regions = [r.model_dump() for r in n.geometry.regions if r.page == page]
        if regions:
            selected.append(
                {
                    "node_id": n.ref,
                    "type": n.node_type,
                    "label": n.label,
                    "regions": regions,
                    "text_raw": n.text_raw,
                    "source_ref": n.source_ref,
                }
            )
    return {"nodes": selected, "notice": "最终逻辑布局；图表成员仅在节点原始详情中查看"}


@router.post("/documents/{document_id}/workbench/auto", status_code=202)
async def auto(sv: ServicesDep, s: SessionDep, document_id: str) -> Any:
    overview_data = await overview(sv, s, document_id)
    result = []
    for fig in overview_data["figures"]:
        if fig["latest"] and fig["latest"]["status"] in {"ok", "remediated", "queued", "running"}:
            continue
        try:
            result.append(await launch(sv, s, document_id, Execution(node_ref=fig["node_ref"])))
        except Exception as exc:
            result.append({"node_ref": fig["node_ref"], "error": str(exc)})
    return {"tasks": result}
