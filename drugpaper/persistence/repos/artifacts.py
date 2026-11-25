"""Artifact 版本与依赖（执行方案 S3.4）。

- `create_version`：同一 work_unit_key 的旧 active 版本改为 superseded，新版本 is_active=true；
  唯一索引 `artifacts(work_unit_key) WHERE is_active` 兜底——并发冲突时抛 IntegrityError，
  由调用方（Submitter）整笔事务重试，保证任何时刻只有一个 active；
- `downstream_of`：递归查询全部下游（供 P8 重跑规划）。
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from sqlalchemy import func, insert, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from drugpaper.domain.artifacts import Artifact
from drugpaper.domain.ids import new_id
from drugpaper.persistence.models import ArtifactDependencyRow, ArtifactRow


def to_domain(r: ArtifactRow) -> Artifact:
    return Artifact(
        artifact_id=r.id,
        document_id=r.document_id,
        artifact_type=r.artifact_type,
        target_ref=r.target_ref,
        work_unit_key=r.work_unit_key,
        version=r.version,
        schema_name=r.schema_name,
        schema_version=r.schema_version,
        payload=r.payload,
        blob_ref=r.blob_ref,
        input_fingerprint=r.input_fingerprint,
        produced_by_run_id=r.run_id,
        status=r.status,
        is_active=r.is_active,
        confidence=r.confidence,
        created_at=r.created_at,
    )


async def create_version(
    s: AsyncSession,
    *,
    document_id: str,
    artifact_type: str,
    target_ref: str,
    work_unit_key: str,
    payload: dict[str, Any] | None,
    input_fingerprint: str | None = None,
    run_id: str | None = None,
    status: str = "completed",
    schema_name: str | None = None,
    schema_version: str | None = None,
    blob_ref: str | None = None,
    confidence: float | None = None,
    user_id: str = "default",
) -> ArtifactRow:
    stmt = select(ArtifactRow).where(
        ArtifactRow.work_unit_key == work_unit_key, ArtifactRow.is_active.is_(True)
    )
    if s.get_bind().dialect.name == "postgresql":
        stmt = stmt.with_for_update()
    current = list((await s.execute(stmt)).scalars())
    latest = (
        await s.execute(
            select(func.max(ArtifactRow.version)).where(ArtifactRow.work_unit_key == work_unit_key)
        )
    ).scalar()
    if current:
        await s.execute(
            update(ArtifactRow)
            .where(ArtifactRow.id.in_([c.id for c in current]))
            .values(is_active=False, status="superseded")
        )
        await s.flush()
    row = ArtifactRow(
        id=new_id("art"),
        user_id=user_id,
        document_id=document_id,
        artifact_type=artifact_type,
        target_ref=target_ref,
        work_unit_key=work_unit_key,
        version=int(latest or 0) + 1,
        schema_name=schema_name,
        schema_version=schema_version,
        payload=payload,
        blob_ref=blob_ref,
        input_fingerprint=input_fingerprint,
        run_id=run_id,
        status=status,
        is_active=True,
        confidence=confidence,
    )
    s.add(row)
    await s.flush()
    return row


async def get_artifact(s: AsyncSession, artifact_id: str) -> ArtifactRow | None:
    return await s.get(ArtifactRow, artifact_id)


async def get_active(s: AsyncSession, work_unit_key: str) -> ArtifactRow | None:
    stmt = select(ArtifactRow).where(
        ArtifactRow.work_unit_key == work_unit_key, ArtifactRow.is_active.is_(True)
    )
    return (await s.execute(stmt)).scalars().first()


async def find_by_fingerprint(s: AsyncSession, work_unit_key: str, fingerprint: str) -> ArtifactRow | None:
    """缓存命中：该 Work Unit 的 active 版本且输入指纹一致（失败的结果不作为缓存）。"""
    row = await get_active(s, work_unit_key)
    if row is not None and row.input_fingerprint == fingerprint and row.status != "failed":
        return row
    return None


async def versions(s: AsyncSession, work_unit_key: str) -> list[ArtifactRow]:
    stmt = select(ArtifactRow).where(ArtifactRow.work_unit_key == work_unit_key).order_by(ArtifactRow.version)
    return list((await s.execute(stmt)).scalars())


async def list_for_target(
    s: AsyncSession,
    document_id: str,
    target_ref: str | None = None,
    *,
    artifact_type: str | None = None,
    active_only: bool = True,
) -> list[ArtifactRow]:
    stmt = select(ArtifactRow).where(ArtifactRow.document_id == document_id)
    if target_ref is not None:
        stmt = stmt.where(ArtifactRow.target_ref == target_ref)
    if artifact_type is not None:
        stmt = stmt.where(ArtifactRow.artifact_type == artifact_type)
    if active_only:
        stmt = stmt.where(ArtifactRow.is_active.is_(True))
    return list((await s.execute(stmt.order_by(ArtifactRow.target_ref, ArtifactRow.version))).scalars())


async def add_dependencies(
    s: AsyncSession, downstream: str, upstreams: Iterable[str | tuple[str, str]], *, user_id: str = "default"
) -> int:
    rows = []
    for u in upstreams:
        up, kind = (u, "input") if isinstance(u, str) else u
        rows.append({"upstream": up, "downstream": downstream, "kind": kind, "user_id": user_id})
    if rows:
        await s.execute(insert(ArtifactDependencyRow), rows)
    return len(rows)


async def upstream_of(s: AsyncSession, artifact_id: str) -> list[tuple[str, str]]:
    rows = await s.execute(
        select(ArtifactDependencyRow.upstream, ArtifactDependencyRow.kind).where(
            ArtifactDependencyRow.downstream == artifact_id
        )
    )
    return [(u, k) for u, k in rows]


async def downstream_of(s: AsyncSession, upstream: str) -> list[str]:
    """递归查出全部下游 artifact_id（`upstream` 可以是 artifact_id 或 `node:<ref>@<hash>`）。"""
    sql = text(
        """
        WITH RECURSIVE down(id) AS (
            SELECT downstream FROM artifact_dependencies WHERE upstream = :u
            UNION
            SELECT d.downstream FROM artifact_dependencies d JOIN down ON d.upstream = down.id
        )
        SELECT id FROM down
        """
    )
    return sorted(r[0] for r in await s.execute(sql, {"u": upstream}))
