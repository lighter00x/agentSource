"""与 Artifact 同事务投影；所有引用重新校验归属，失败整份回滚。"""

from __future__ import annotations

from typing import Any

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from drugpaper.chem.esmiles import assert_roundtrip
from drugpaper.domain.geometry import BBox, Geometry, Region, local_to_page
from drugpaper.domain.ids import new_id, parse_ref
from drugpaper.facts.compounds import Compound, RGroupAssignment, StructureCandidate
from drugpaper.facts.labels import normalize
from drugpaper.persistence.models import (
    ArtifactRow,
    ChemistryConflictRow,
    CompoundAliasRow,
    CompoundRow,
    Document,
    LabelClaimRow,
    MarkushScaffoldRow,
    NodeRow,
    RGroupAssignmentRow,
    StructureCandidateRow,
)
from drugpaper.skills.protocol import SkillContext, SkillResult

FACT_MODELS = {
    "structure_candidate": StructureCandidateRow,
    "label_claim": LabelClaimRow,
    "markush_scaffold": MarkushScaffoldRow,
    "rgroup_assignment": RGroupAssignmentRow,
    "chemistry_conflict": ChemistryConflictRow,
}


async def owned_node(session: AsyncSession, ctx: SkillContext, ref: str) -> NodeRow:
    parsed = parse_ref(ref)
    if parsed is None or parsed[:2] != (ctx.document_id, ctx.revision):
        raise PermissionError("化学事实跨文档或 revision 引用")
    node = await session.get(NodeRow, parsed)
    if node is None or node.user_id != ctx.user_id:
        raise PermissionError("化学事实引用不可见节点")
    return node


async def project(
    session: AsyncSession, artifact_id: str, result: SkillResult, ctx: SkillContext, skill_name: str
) -> None:
    if skill_name not in {
        "molecule_detect",
        "molecule_recognize",
        "mol_backfill",
        "markush_assemble",
        "compound_resolve",
        "sar_record",
        "name_to_structure",
    }:
        return
    if not result.node_proposals and not result.fact_proposals and skill_name != "name_to_structure":
        return
    document = await session.get(Document, ctx.document_id)
    if document is None or document.user_id != ctx.user_id:
        raise PermissionError("化学投影文档不可见")
    if document.active_revision != ctx.revision:
        raise ValueError("文档 revision 已改变，请重新运行")
    if skill_name == "compound_resolve" and ctx.materials:
        for mention in ctx.materials.target.get("text_mentions", []) + ctx.materials.target.get(
            "name_sources", []
        ):
            source = await session.get(ArtifactRow, mention["artifact_id"])
            node = await owned_node(session, ctx, mention["node_ref"])
            if (
                source is None
                or not source.is_active
                or source.status == "failed"
                or node.status == "removed"
                or node.text_raw != mention["text_raw"]
                or node.section_role != mention["section_role"]
            ):
                raise ValueError("正文归一材料已改变，请重新运行")
        for expected_structure in ctx.materials.target.get("structures", []):
            current_structure = await session.get(StructureCandidateRow, expected_structure["structure_id"])
            if (
                current_structure is None
                or not current_structure.is_active
                or current_structure.payload != expected_structure
            ):
                raise ValueError("结构候选已改变，请重新归一")
    if ctx.materials and ctx.materials.target.get("type") == "molecule":
        current_node = await owned_node(session, ctx, ctx.target_ref)
        if current_node.geometry != ctx.materials.target.get("geometry"):
            raise ValueError("分子几何已改变，请重跑识别")
    if result.node_proposals:
        parent = await owned_node(session, ctx, ctx.target_ref)
        if ctx.materials and parent.geometry.get("regions", []) != ctx.materials.target.get("regions", []):
            raise ValueError("父节点几何已改变")
        current_children = list(
            (
                await session.execute(
                    select(NodeRow).where(
                        NodeRow.document_id == ctx.document_id,
                        NodeRow.revision == ctx.revision,
                        NodeRow.parent_local_id == parent.local_id,
                        NodeRow.node_type == "molecule",
                    )
                )
            ).scalars()
        )
        current_geometry = {n.local_id: n.geometry for n in current_children}
        expected_geometry = (
            {n["local_id"]: n["geometry"] for n in ctx.materials.target.get("children", [])}
            if ctx.materials
            else {}
        )
        if current_geometry != expected_geometry:
            raise ValueError("分子检测结果已被另一任务更新，请重跑")
        active_ids = []
        for proposal in result.node_proposals:
            regions = parent.geometry["regions"]
            parent_region = Region.model_validate(regions[proposal.parent_region_index])
            bbox = BBox(
                x0=proposal.local_bbox[0],
                y0=proposal.local_bbox[1],
                x1=proposal.local_bbox[2],
                y1=proposal.local_bbox[3],
            )
            row = await session.get(NodeRow, (ctx.document_id, ctx.revision, proposal.local_id))
            if row is not None and (
                row.user_id != ctx.user_id
                or row.parent_local_id != parent.local_id
                or row.node_type != "molecule"
            ):
                raise PermissionError("子节点冲突或越权")
            if row is not None and row.attributes.get("locked_fields"):
                raise PermissionError("分子节点已人工锁定")
            if row is None:
                row = NodeRow(
                    document_id=ctx.document_id,
                    revision=ctx.revision,
                    local_id=proposal.local_id,
                    user_id=ctx.user_id,
                    node_type=proposal.node_type,
                    parent_local_id=parent.local_id,
                )
                session.add(row)
            moved = row.geometry and row.geometry.get("local_bbox") != bbox.model_dump()
            row.geometry = Geometry(
                local_bbox=bbox,
                parent_region_index=proposal.parent_region_index,
                regions=[
                    Region(
                        page=parent_region.page, bbox=local_to_page(bbox, parent_region.bbox), derived=True
                    )
                ],
                derived=True,
            ).model_dump(mode="json")
            row.page, row.status, row.source = parent_region.page, proposal.status, "skill"
            row.reading_order = proposal.attributes.get("reading_index", 0)
            row.attributes = {**(row.attributes or {}), **proposal.attributes}
            row.source_ref = {"artifact_id": artifact_id}
            if moved or proposal.status == "removed":
                ref = f"{ctx.document_id}/r{ctx.revision}/{proposal.local_id}"
                await session.execute(
                    update(StructureCandidateRow)
                    .where(
                        StructureCandidateRow.node_ref == ref, StructureCandidateRow.user_id == ctx.user_id
                    )
                    .values(is_active=False)
                )
                row.attributes = {
                    k: v for k, v in row.attributes.items() if k not in {"structure_id", "molecule_type"}
                }
            if proposal.status != "removed":
                active_ids.append(proposal.local_id)
        parent.children = [i for i in parent.children or [] if ".m" not in i] + active_ids
    # 同一 Work Unit 的旧事实保留为历史；局部重跑不触碰兄弟分子。
    replace_map: dict[str, list[Any]] = {
        "molecule_recognize": [StructureCandidateRow],
        "mol_backfill": [LabelClaimRow],
        "markush_assemble": [StructureCandidateRow, MarkushScaffoldRow, RGroupAssignmentRow, LabelClaimRow],
        "compound_resolve": [ChemistryConflictRow],
        "sar_record": [StructureCandidateRow, MarkushScaffoldRow, RGroupAssignmentRow, LabelClaimRow],
        "name_to_structure": [StructureCandidateRow, LabelClaimRow],
    }
    current_artifact = await session.get(ArtifactRow, artifact_id)
    assert current_artifact is not None
    previous_ids = select(ArtifactRow.id).where(
        ArtifactRow.work_unit_key == current_artifact.work_unit_key, ArtifactRow.id != artifact_id
    )
    for model in replace_map.get(skill_name, []):
        statement = update(model).where(model.artifact_id.in_(previous_ids), model.user_id == ctx.user_id)
        if model is ChemistryConflictRow:
            recalculated = [
                f.payload["compound_id"] for f in result.fact_proposals if f.fact_type == "compound"
            ]
            statement = statement.where(model.compound_id.in_(recalculated))
        await session.execute(statement.values(is_active=False))
    for fact in result.fact_proposals:
        data = dict(fact.payload)
        kind = fact.fact_type
        for evidence in data.get("evidence", []):
            await owned_node(session, ctx, evidence["node_id"])
        if kind in FACT_MODELS and not any(
            e.get("role", "primary") == "primary" for e in data.get("evidence", [])
        ):
            raise ValueError("V-E01: 化学事实缺少 primary 证据")
        if kind == "molecule_update":
            node = await owned_node(session, ctx, data["node_ref"])
            if (
                node.node_type != "molecule"
                or node.status == "removed"
                or not (
                    data["node_ref"] == ctx.target_ref
                    or node.parent_local_id == ctx.target_ref.rsplit("/", 1)[-1]
                )
            ):
                raise PermissionError("分子更新目标越权")
            if node.attributes.get("locked_fields"):
                raise PermissionError("分子已锁定")
            node.attributes = {**node.attributes, **data["attributes"]}
            node.status = data.get("status", node.status)
            continue
        if kind == "compound":
            compound = Compound.model_validate(data)
            if compound.document_id != ctx.document_id:
                raise PermissionError("化合物跨文档")
            fact_row = await session.get(CompoundRow, compound.compound_id)
            if fact_row and (fact_row.document_id != ctx.document_id or fact_row.user_id != ctx.user_id):
                raise PermissionError("化合物不可见")
            if fact_row and ctx.materials:
                expected = next(
                    (
                        c
                        for c in ctx.materials.target.get("compounds", [])
                        if c["compound_id"] == compound.compound_id
                    ),
                    None,
                )
                if expected is not None and expected != fact_row.payload:
                    raise ValueError("化合物台账或人工锁已经改变，请重新归一")
            if fact_row is None:
                fact_row = CompoundRow(
                    id=compound.compound_id,
                    document_id=ctx.document_id,
                    user_id=ctx.user_id,
                    primary_label=compound.primary_label,
                )
                session.add(fact_row)
            fact_row.accepted_structure_id, fact_row.stereo_parent_id, fact_row.payload = (
                compound.accepted_structure_id,
                compound.stereo_parent_id,
                compound.model_dump(),
            )
            await session.flush()
            await session.execute(
                delete(CompoundAliasRow).where(
                    CompoundAliasRow.compound_id == fact_row.id,
                    CompoundAliasRow.user_id == ctx.user_id,
                    CompoundAliasRow.alias.not_in(compound.aliases),
                )
            )
            for alias in compound.aliases:
                found = (
                    await session.execute(
                        select(CompoundAliasRow).where(
                            CompoundAliasRow.compound_id == fact_row.id, CompoundAliasRow.alias == alias
                        )
                    )
                ).scalar_one_or_none()
                if found is None:
                    session.add(
                        CompoundAliasRow(
                            id=new_id("alias"), user_id=ctx.user_id, compound_id=fact_row.id, alias=alias
                        )
                    )
            continue
        if kind == "structure_binding":
            structure_row = await session.get(StructureCandidateRow, data["structure_id"])
            compound_structure_row = await session.get(CompoundRow, data["compound_id"])
            if (
                structure_row is None
                or compound_structure_row is None
                or structure_row.document_id != ctx.document_id
                or structure_row.user_id != ctx.user_id
                or compound_structure_row.document_id != ctx.document_id
                or compound_structure_row.user_id != ctx.user_id
            ):
                raise PermissionError("结构绑定越权")
            structure_row.compound_id = compound_structure_row.id
            structure_row.payload = {**structure_row.payload, "compound_id": compound_structure_row.id}
            continue
        model = FACT_MODELS.get(kind)
        if model is None:
            continue
        extra: dict[str, Any] = {}
        if kind == "structure_candidate":
            candidate = StructureCandidate.model_validate(data)
            await owned_node(session, ctx, candidate.node_ref)
            if not (
                candidate.node_ref == ctx.target_ref or candidate.node_ref.startswith(ctx.target_ref + ".")
            ):
                raise PermissionError("结构候选目标越权")
            extra = {
                "source": candidate.source,
                "node_ref": candidate.node_ref,
                "compound_id": candidate.compound_id,
            }
            identity = candidate.structure_id
            data = candidate.model_dump()
            assert_roundtrip(fact.payload.get("e_smiles_raw") or "", data.get("e_smiles_raw") or "")
        else:
            identity = (
                str(data.get("assignment_id"))
                if kind == "rgroup_assignment"
                else str(data.get("scaffold_id"))
                if kind == "markush_scaffold"
                else new_id("fact")
            )
            if not data.get("evidence"):
                raise ValueError("V-E01: 化学事实缺少 primary 证据")
            if kind == "label_claim":
                await owned_node(session, ctx, data["node_ref"])
                structure = await session.get(StructureCandidateRow, data.get("structure_id"))
                if (
                    structure is None
                    or structure.user_id != ctx.user_id
                    or structure.document_id != ctx.document_id
                ):
                    raise PermissionError("标签引用不可见候选")
                extra = {"label": normalize(data["label"]), "structure_id": data.get("structure_id")}
            elif kind == "chemistry_conflict":
                extra = {"compound_id": data["compound_id"]}
            elif kind == "rgroup_assignment":
                RGroupAssignment.model_validate(data)
        session.add(
            model(
                id=identity,
                user_id=ctx.user_id,
                document_id=ctx.document_id,
                artifact_id=artifact_id,
                target_ref=ctx.target_ref,
                is_active=True,
                payload=data,
                **extra,
            )
        )
        await session.flush()
