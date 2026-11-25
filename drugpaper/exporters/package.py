from __future__ import annotations

import asyncio
import csv
import hashlib
import io
import json
import zipfile
from pathlib import Path
from typing import Any

from sqlalchemy import select

from drugpaper import __version__
from drugpaper.domain.ids import new_id
from drugpaper.exporters.chunks import build_chunks
from drugpaper.exporters.markdown import audit_markdown, corpus_markdown
from drugpaper.exporters.models import ExportManifest
from drugpaper.facts.full_text import load_context
from drugpaper.persistence.models import ExportRow, FullTextResultRow, RunRow
from drugpaper.profiles.projector import PROFILE_ROOT, ProjectionResult
from drugpaper.services import Services
from drugpaper.validators.projection import validate_projection


def json_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, default=str) + "\n").encode()


def csv_bytes(rows: list[dict[str, Any]]) -> bytes:
    fields = list(dict.fromkeys(key for row in rows for key in row))
    out = io.StringIO(newline="")
    writer = csv.DictWriter(
        out, fields or ["measurement_id", "subject_label", "metric", "value_raw", "conditions", "evidence"]
    )
    writer.writeheader()
    for row in rows:
        writer.writerow(
            {
                k: json.dumps(v, ensure_ascii=False) if isinstance(v, list | dict) else v
                for k, v in row.items()
            }
        )
    return out.getvalue().encode()


def _archive(files: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, data in sorted(files.items()):
            info = zipfile.ZipInfo(name, date_time=(2026, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, data)
    return buffer.getvalue()


def component_hashes() -> dict[str, str]:
    root = Path(__file__).resolve().parents[2]
    paths = [
        *(p for p in root.joinpath("drugpaper").rglob("*") if p.suffix in {".py", ".md", ".yaml", ".json"}),
        *root.joinpath("prompts", "skills").rglob("*.md"),
        *root.joinpath("config").glob("*.yaml"),
        *PROFILE_ROOT.glob("*.json"),
        *PROFILE_ROOT.glob("*.yaml"),
    ]
    return {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(set(paths))}


async def create_export(
    services: Services, document_id: str, *, user_id: str = "default", result_id: str | None = None
) -> dict[str, Any]:
    ctx = await load_context(services.db, document_id, user_id=user_id)
    async with services.db.session(user_id) as session:
        query = select(FullTextResultRow).where(
            FullTextResultRow.user_id == user_id,
            FullTextResultRow.document_id == document_id,
            FullTextResultRow.revision == ctx.ir.revision,
            FullTextResultRow.fingerprint == ctx.fingerprint,
        )
        if result_id:
            query = query.where(FullTextResultRow.id == result_id)
        row = (
            await session.execute(
                query.order_by(FullTextResultRow.created_at.desc(), FullTextResultRow.id.desc()).limit(1)
            )
        ).scalar_one_or_none()
        if row is None:
            raise ValueError("current full-text result unavailable; run full-text understanding first")
        payload, result_id, status = row.payload, row.id, row.status
        runs = (
            (
                await session.execute(
                    select(RunRow)
                    .where(RunRow.user_id == user_id, RunRow.document_id == document_id)
                    .order_by(RunRow.started_at, RunRow.id)
                )
            )
            .scalars()
            .all()
        )
        models = [r.model for r in runs if r.model]
        metrics = [
            {"run_id": r.id, "component": r.component, "metrics": r.metrics} for r in runs if r.metrics
        ]
    projected = ProjectionResult.model_validate(payload["projection"])
    checks = validate_projection(projected, ctx)
    blocking = [c for c in checks if c.code in {"V-J01", "V-J03", "V-E01"}]
    if blocking:
        raise ValueError(
            "invalid schema/raw SMILES/evidence blocks export: " + "; ".join(c.code for c in blocking)
        )
    files = {
        "result.drug_optimization_v1.json": json_bytes(projected.delivery),
        "result.drug_optimization_v1.audit.json": json_bytes(projected.audit),
        "document.json": json_bytes(ctx.ir.model_dump(mode="json")),
        "compounds.json": json_bytes(ctx.compounds),
    }
    images = {}
    store = services.store_for(user_id)
    for node in ctx.ir.nodes:
        blob = node.attributes.get("image_blob")
        if node.status != "removed" and blob:
            data = await store.get(blob)
            suffix = "jpg" if data.startswith(b"\xff\xd8") else "png"
            name = f"images/{blob}.{suffix}"
            files[name] = data
            images[node.ref] = name
    files["paper.corpus.md"] = corpus_markdown(ctx, images).encode()
    files["paper.audit.md"] = audit_markdown(ctx, payload).encode()
    assays = {a["assay_id"]: a for a in ctx.assays}
    files["measurements.csv"] = csv_bytes(
        [
            {
                **m,
                "conditions": assays.get(m.get("assay_id"), {}).get("conditions", {}),
                "species": assays.get(m.get("assay_id"), {}).get("species"),
            }
            for m in ctx.measurements
        ]
    )
    for ref, report in ctx.reports.items():
        extraction = report.get("extraction", {})
        if extraction.get("series"):
            local = ref.rsplit("/", 1)[-1]
            files[f"charts/{local}.csv"] = csv_bytes(
                [
                    {"series": s["label"], "x": p[0], "y": p[1], "node_ref": ref}
                    for s in extraction["series"]
                    for p in s.get("points", [])
                ]
            )
            overlay = (
                extraction.get("overlay_blob")
                or extraction.get("overlay_blob_ref")
                or report.get("overlay_blob_ref")
            )
            if overlay:
                files[f"charts/{local}.overlay.png"] = await store.get(overlay)
    files["chunks.jsonl"] = b"".join(
        (json.dumps(c, ensure_ascii=False) + "\n").encode() for c in build_chunks(ctx, projected.audit)
    )
    warnings = list(
        dict.fromkeys(
            [
                *projected.warnings,
                *projected.audit.get("_audit", {}).get("warnings", []),
                *[c.message for c in checks],
            ]
        )
    )
    manifest = {
        "format_version": "0.6.1",
        "app_version": __version__,
        "profile": "drug_optimization_v1",
        "profile_version": "0.6.1",
        "document_id": document_id,
        "revision": ctx.ir.revision,
        "fingerprint": ctx.fingerprint,
        "result_id": result_id,
        "status": status,
        "components": await asyncio.to_thread(component_hashes),
        "artifacts": ctx.artifacts,
        "models": models,
        "call_metrics": metrics,
        "checks": payload.get("checks", []),
        "warnings": warnings,
        "files": {
            name: {"sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)}
            for name, data in sorted(files.items())
        },
    }
    ExportManifest.model_validate(manifest)
    files["manifest.json"] = json_bytes(manifest)
    if (await load_context(services.db, document_id, user_id=user_id)).fingerprint != ctx.fingerprint:
        raise ValueError("source changed during export")
    archive = await asyncio.to_thread(_archive, files)
    blob = await store.put(archive, "application/zip")
    export_id = new_id("export")
    async with services.db.session(user_id) as session:
        session.add(
            ExportRow(
                id=export_id,
                user_id=user_id,
                document_id=document_id,
                result_id=result_id,
                blob_ref=blob,
                payload=manifest,
            )
        )
    return {
        "export_id": export_id,
        "document_id": document_id,
        "result_id": result_id,
        "status": status,
        "blob_ref": blob,
        "files": sorted(files),
        "warnings": warnings,
        "download_url": f"/api/v1/exports/{export_id}/download",
    }
