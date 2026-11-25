"""Project validated T1 spans to the existing normalized measurement ledger."""

from __future__ import annotations

from typing import Any

from pint.errors import PintError
from sqlalchemy import select

from drugpaper.domain.ids import new_id
from drugpaper.facts.figure_models import Assay, Measurement
from drugpaper.facts.full_text import load_context
from drugpaper.facts.metric_dictionary import MetricDictionary
from drugpaper.facts.table_processing import conditions
from drugpaper.facts.units import to_si
from drugpaper.facts.value_parser import parse
from drugpaper.persistence.db import Database
from drugpaper.persistence.models import AssayRow, MeasurementRow


async def project_text_measurements(
    db: Database, document_id: str, *, user_id: str = "default"
) -> dict[str, int]:
    ctx = await load_context(db, document_id, user_id=user_id)
    dictionary = MetricDictionary()
    artifact_ids = {a["target_ref"]: a["id"] for a in ctx.artifacts if a["type"] == "text_extraction"}
    aliases: dict[str, set[str]] = {}
    for c in ctx.compounds:
        for label in [c["primary_label"], *c.get("aliases", [])]:
            aliases.setdefault(label, set()).add(c["compound_id"])
    count = review = 0
    async with db.session(user_id) as session:
        for ref, payload in ctx.extractions.items():
            aid = artifact_ids[ref]
            # Idempotence: one projection per immutable source artifact, transactional with assays.
            existing = (
                await session.execute(
                    select(MeasurementRow.id).where(
                        MeasurementRow.artifact_id == aid, MeasurementRow.user_id == user_id
                    )
                )
            ).first()
            if existing:
                continue
            for item in payload.get("measurements", []):
                metric = dictionary.match(item["metric_text"], item.get("conditions") or "")
                parsed = parse(item["value_raw"])
                unit = item.get("unit") or parsed.unit
                evidence = [{**e, "artifact_id": aid} for e in item["evidence"]]
                ids = aliases.get(item["compound_label"], set())
                status = (
                    "explicit"
                    if parsed.status == "explicit"
                    and not metric.get("normalization_warning")
                    and payload.get("status") == "completed"
                    and len(ids) == 1
                    else "needs_review"
                )
                value_si = None
                if parsed.value is not None and unit:
                    try:
                        value_si = to_si(parsed.value, unit)
                    except (PintError, ValueError, TypeError):
                        status = "needs_review"
                assay_id = new_id("assay")
                cond = conditions(item.get("conditions") or "")
                assay = Assay(
                    assay_id=assay_id,
                    document_id=document_id,
                    name_raw=item["metric_text"],
                    assay_type=metric["id"],
                    conditions=cond,
                    evidence=evidence,
                )
                measurement = Measurement(
                    measurement_id=new_id("meas"),
                    document_id=document_id,
                    compound_id=next(iter(ids)) if len(ids) == 1 else None,
                    subject_label=item["compound_label"],
                    assay_id=assay_id,
                    metric=metric["id"],
                    metric_label_raw=item["metric_text"],
                    value_raw=item["value_raw"],
                    qualifier=parsed.qualifier,
                    value=parsed.value,
                    value_upper=parsed.value_upper,
                    error=parsed.error,
                    unit=unit,
                    unit_raw=unit,
                    value_si=value_si,
                    status=status,
                    source_kind="text",
                    evidence=evidence,
                    produced_by_artifact_id=aid,
                    normalization_warning=metric.get("normalization_warning"),
                )
                common: dict[str, Any] = {
                    "user_id": user_id,
                    "document_id": document_id,
                    "artifact_id": aid,
                    "target_ref": ref,
                    "is_active": True,
                }
                session.add(AssayRow(id=assay_id, payload=assay.model_dump(mode="json"), **common))
                session.add(
                    MeasurementRow(
                        id=measurement.measurement_id, payload=measurement.model_dump(mode="json"), **common
                    )
                )
                count += 1
                review += status == "needs_review"
    return {"measurements": count, "needs_review": review}
