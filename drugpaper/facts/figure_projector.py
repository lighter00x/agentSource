from __future__ import annotations

from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from drugpaper.domain.ids import new_id
from drugpaper.facts.figure_models import Assay, Measurement
from drugpaper.facts.projector import owned_node
from drugpaper.persistence.models import ArtifactRow, AssayRow, Document, FigureRelationRow, MeasurementRow
from drugpaper.skills.protocol import SkillContext, SkillResult


async def project(session: AsyncSession, artifact_id: str, result: SkillResult, ctx: SkillContext) -> None:
    if ctx.skill_id.split("@")[0] == "panel_split":
        await project_panels(session, result, ctx)
        return
    if ctx.skill_id.split("@")[0] not in {"measurement_map", "chart_measurements", "scheme_relations"}:
        return
    kinds: dict[str, Any] = {
        "assay": AssayRow,
        "measurement": MeasurementRow,
        "figure_relation": FigureRelationRow,
    }
    proposals = [p for p in result.fact_proposals if p.fact_type in kinds]
    if not proposals:
        return
    doc = await session.get(Document, ctx.document_id)
    if doc is None or doc.user_id != ctx.user_id or doc.active_revision != ctx.revision:
        raise PermissionError("P5 facts ownership/revision mismatch")
    artifact = await session.get(ArtifactRow, artifact_id)
    assert artifact is not None
    old = select(ArtifactRow.id).where(
        ArtifactRow.work_unit_key == artifact.work_unit_key, ArtifactRow.id != artifact_id
    )
    for model in kinds.values():
        await session.execute(
            update(model)
            .where(model.artifact_id.in_(old), model.user_id == ctx.user_id)
            .values(is_active=False)
        )
    for proposal in proposals:
        payload = dict(proposal.payload)
        if not payload.get("evidence") or not any(
            e.get("role", "primary") == "primary" for e in payload["evidence"]
        ):
            raise ValueError("V-E01: figure fact missing primary evidence")
        for evidence in payload["evidence"]:
            await owned_node(session, ctx, evidence["node_id"])
        if proposal.fact_type == "measurement":
            payload = Measurement.model_validate(payload).model_dump()
            payload["produced_by_artifact_id"] = artifact_id
        if proposal.fact_type == "assay":
            payload = Assay.model_validate(payload).model_dump()
        session.add(
            kinds[proposal.fact_type](
                id=new_id("fact"),
                user_id=ctx.user_id,
                document_id=ctx.document_id,
                artifact_id=artifact_id,
                target_ref=ctx.target_ref,
                is_active=True,
                payload=payload,
            )
        )


async def project_panels(session: AsyncSession, result: SkillResult, ctx: SkillContext) -> None:
    from drugpaper.domain.geometry import BBox, Geometry, Region, local_to_page
    from drugpaper.persistence.models import NodeRow

    doc = await session.get(Document, ctx.document_id)
    if doc is None or doc.user_id != ctx.user_id or doc.active_revision != ctx.revision:
        raise PermissionError("panel ownership/revision mismatch")
    parent = await owned_node(session, ctx, ctx.target_ref)
    if parent.attributes.get("locked_fields"):
        raise PermissionError("panel parent locked")
    children = []
    for proposal in result.node_proposals:
        if proposal.node_type != "chart_panel" or proposal.parent_local_id != parent.local_id:
            raise PermissionError("invalid panel parent")
        b = BBox(
            x0=proposal.local_bbox[0],
            y0=proposal.local_bbox[1],
            x1=proposal.local_bbox[2],
            y1=proposal.local_bbox[3],
        )
        region = Region.model_validate(parent.geometry["regions"][proposal.parent_region_index])
        row = await session.get(NodeRow, (ctx.document_id, ctx.revision, proposal.local_id))
        if row and (
            row.user_id != ctx.user_id
            or row.node_type != "chart_panel"
            or row.parent_local_id != parent.local_id
        ):
            raise PermissionError("panel ID collision")
        if row and row.attributes.get("locked_fields"):
            raise PermissionError("panel locked")
        if not row:
            row = NodeRow(
                document_id=ctx.document_id,
                revision=ctx.revision,
                local_id=proposal.local_id,
                user_id=ctx.user_id,
                node_type="chart_panel",
                parent_local_id=parent.local_id,
            )
            session.add(row)
        row.geometry = Geometry(
            local_bbox=b,
            parent_region_index=proposal.parent_region_index,
            regions=[Region(page=region.page, bbox=local_to_page(b, region.bbox))],
        ).model_dump()
        row.attributes = {**(row.attributes or {}), "panel_of": ctx.target_ref}
        row.status = "active"
        children.append(row.local_id)
    old = list(
        (
            await session.execute(
                select(NodeRow).where(
                    NodeRow.document_id == ctx.document_id,
                    NodeRow.revision == ctx.revision,
                    NodeRow.parent_local_id == parent.local_id,
                    NodeRow.node_type == "chart_panel",
                )
            )
        ).scalars()
    )
    for row in old:
        if row.local_id not in children:
            row.status = "removed"
    parent.children = [key for key in parent.children or [] if ".panel" not in key] + children
