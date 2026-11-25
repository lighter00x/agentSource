"""SQLAlchemy 模型：文档层与过程层的核心表（设计文档 16.1 节）。

- 所有表带 `user_id TEXT NOT NULL DEFAULT 'default'`（P7 启用 RLS 与用户隔离）；
- JSON 字段在 PostgreSQL 上是 JSONB，在 SQLite 上是 JSON；
- `nodes.tsv`（TSVECTOR）只在 PostgreSQL 上存在，由迁移 0002 创建，ORM 不映射。
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

JsonType = JSON().with_variant(JSONB(), "postgresql")
DEFAULT_USER = "default"


def utcnow() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    type_annotation_map = {dict[str, Any]: JsonType, list[Any]: JsonType}


def _user_col() -> Mapped[str]:
    return mapped_column(String(64), nullable=False, default=DEFAULT_USER, server_default=text("'default'"))


class Document(Base):
    __tablename__ = "documents"
    __table_args__ = (UniqueConstraint("user_id", "sha256", name="uq_documents_user_sha256"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = _user_col()
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    filename: Mapped[str | None] = mapped_column(Text)
    title: Mapped[str | None] = mapped_column(Text)
    doi: Mapped[str | None] = mapped_column(Text)
    # uploaded → parsing → parsed → ir_built → ready；失败为 failed
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="uploaded")
    active_revision: Mapped[int | None] = mapped_column(Integer)
    batch_id: Mapped[str | None] = mapped_column(String(64))
    config: Mapped[dict[str, Any]] = mapped_column(JsonType, default=dict)
    meta: Mapped[dict[str, Any]] = mapped_column(JsonType, default=dict)
    quality_report: Mapped[dict[str, Any]] = mapped_column(JsonType, default=dict)
    error: Mapped[dict[str, Any] | None] = mapped_column(JsonType)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ParseRevision(Base):
    __tablename__ = "parse_revisions"

    document_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("documents.id", ondelete="CASCADE"), primary_key=True
    )
    revision: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[str] = _user_col()
    parser: Mapped[str] = mapped_column(String(64), nullable=False)
    parser_version: Mapped[str] = mapped_column(String(32), nullable=False)
    raw_blob_refs: Mapped[dict[str, Any]] = mapped_column(JsonType, default=dict)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="created")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Asset(Base):
    __tablename__ = "assets"
    __table_args__ = (Index("ix_assets_document_kind", "document_id", "kind"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = _user_col()
    document_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("documents.id", ondelete="CASCADE"), nullable=False
    )
    # origin_pdf / raw_layout_json / raw_middle_json / raw_content_list / raw_md / image / merged_image / page_image / crop / other
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    blob_ref: Mapped[str] = mapped_column(String(64), nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    content_type: Mapped[str | None] = mapped_column(String(128))
    path: Mapped[str | None] = mapped_column(Text)  # 解析产物中的相对路径（images/xxx.jpg）
    meta: Mapped[dict[str, Any]] = mapped_column(JsonType, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class NodeRow(Base):
    __tablename__ = "nodes"
    __table_args__ = (
        Index("ix_nodes_doc_rev_type", "document_id", "revision", "node_type"),
        Index("ix_nodes_doc_rev_page", "document_id", "revision", "page"),
    )

    document_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("documents.id", ondelete="CASCADE"), primary_key=True
    )
    revision: Mapped[int] = mapped_column(Integer, primary_key=True)
    local_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    user_id: Mapped[str] = _user_col()
    node_type: Mapped[str] = mapped_column(String(32), nullable=False)
    subtype: Mapped[str | None] = mapped_column(String(64))
    parent_local_id: Mapped[str | None] = mapped_column(String(128))
    children: Mapped[list[Any]] = mapped_column(JsonType, default=list)
    reading_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    page: Mapped[int | None] = mapped_column(Integer)  # 第一个 region 的页码（冗余，便于按页查询）
    geometry: Mapped[dict[str, Any]] = mapped_column(JsonType, default=dict)
    label: Mapped[str | None] = mapped_column(Text)
    text_raw: Mapped[str | None] = mapped_column(Text)
    text_norm: Mapped[str | None] = mapped_column(Text)
    section_role: Mapped[str | None] = mapped_column(String(64))
    source: Mapped[str] = mapped_column(String(16), nullable=False, default="mineru")
    source_ref: Mapped[dict[str, Any]] = mapped_column(JsonType, default=dict)
    quality_flags: Mapped[list[Any]] = mapped_column(JsonType, default=list)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active")
    attributes: Mapped[dict[str, Any]] = mapped_column(JsonType, default=dict)


class RelationRow(Base):
    __tablename__ = "relations"
    __table_args__ = (Index("ix_relations_doc_rev", "document_id", "revision"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = _user_col()
    document_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("documents.id", ondelete="CASCADE"), nullable=False
    )
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    source: Mapped[str] = mapped_column(String(128), nullable=False)
    target: Mapped[str] = mapped_column(String(128), nullable=False)
    type: Mapped[str] = mapped_column(String(32), nullable=False)
    evidence: Mapped[dict[str, Any]] = mapped_column(JsonType, default=dict)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active")


class RunRow(Base):
    __tablename__ = "runs"
    __table_args__ = (Index("ix_runs_parent", "parent_run_id"), Index("ix_runs_trace", "trace_id"))

    id: Mapped[str] = mapped_column(String(96), primary_key=True)
    user_id: Mapped[str] = _user_col()
    document_id: Mapped[str | None] = mapped_column(String(64))
    # pipeline / agent / model_call / tool / validation / skill / chat_turn / rerun
    run_type: Mapped[str] = mapped_column(String(32), nullable=False)
    component: Mapped[str] = mapped_column(String(128), nullable=False)
    component_version: Mapped[str | None] = mapped_column(String(64))
    component_hash: Mapped[str | None] = mapped_column(String(64))
    inputs: Mapped[dict[str, Any]] = mapped_column(JsonType, default=dict)
    outputs: Mapped[dict[str, Any]] = mapped_column(JsonType, default=dict)
    model: Mapped[dict[str, Any] | None] = mapped_column(JsonType)
    parent_run_id: Mapped[str | None] = mapped_column(String(96))
    trace_id: Mapped[str | None] = mapped_column(String(96))
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="running")
    error: Mapped[dict[str, Any] | None] = mapped_column(JsonType)
    metrics: Mapped[dict[str, Any]] = mapped_column(JsonType, default=dict)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ArtifactRow(Base):
    __tablename__ = "artifacts"
    __table_args__ = (
        Index(
            "uq_artifacts_active_work_unit",
            "work_unit_key",
            unique=True,
            postgresql_where=text("is_active"),
            sqlite_where=text("is_active"),
        ),
        Index("ix_artifacts_doc_target", "document_id", "target_ref"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = _user_col()
    document_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("documents.id", ondelete="CASCADE"), nullable=False
    )
    artifact_type: Mapped[str] = mapped_column(String(64), nullable=False)
    target_ref: Mapped[str] = mapped_column(String(256), nullable=False)
    work_unit_key: Mapped[str] = mapped_column(String(256), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    schema_name: Mapped[str | None] = mapped_column(String(128))
    schema_version: Mapped[str | None] = mapped_column(String(32))
    payload: Mapped[dict[str, Any] | None] = mapped_column(JsonType)
    blob_ref: Mapped[str | None] = mapped_column(String(64))
    input_fingerprint: Mapped[str | None] = mapped_column(String(64))
    run_id: Mapped[str | None] = mapped_column(String(96))
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="completed")
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    confidence: Mapped[float | None] = mapped_column(Float)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ArtifactDependencyRow(Base):
    __tablename__ = "artifact_dependencies"
    __table_args__ = (
        Index("ix_artifact_deps_upstream", "upstream"),
        Index("ix_artifact_deps_downstream", "downstream"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = _user_col()
    upstream: Mapped[str] = mapped_column(String(256), nullable=False)
    downstream: Mapped[str] = mapped_column(String(64), nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False, default="input")


# ---------------------------------------------------------------- P3：Skill 与能力（迁移 0003、0004）


class SkillVersionRow(Base):
    __tablename__ = "skill_versions"
    __table_args__ = (
        UniqueConstraint(
            "name", "version", "content_hash", "scope", "owner_user_id", name="uq_skill_versions_identity"
        ),
        Index("ix_skill_versions_name_status", "name", "status"),
        Index(
            "uq_skill_active_scope",
            "name",
            "scope",
            "owner_user_id",
            unique=True,
            postgresql_where=text("status = 'active'"),
            sqlite_where=text("status = 'active'"),
        ),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = _user_col()
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    version: Mapped[str] = mapped_column(String(32), nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    scope: Mapped[str] = mapped_column(String(16), nullable=False)  # builtin / system / user
    owner_user_id: Mapped[str] = mapped_column(
        String(64), nullable=False, default="", server_default=text("''")
    )
    source: Mapped[str] = mapped_column(String(16), nullable=False, default="manual")  # manual / agent_code
    # discovered / validating / testing / staged / active / invalid / disabled
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    path: Mapped[str | None] = mapped_column(Text)
    manifest: Mapped[dict[str, Any]] = mapped_column(JsonType, default=dict)
    test_report: Mapped[dict[str, Any]] = mapped_column(JsonType, default=dict)
    errors: Mapped[list[Any]] = mapped_column(JsonType, default=list)
    warnings: Mapped[list[Any]] = mapped_column(JsonType, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    activated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class CapabilityIndexRow(Base):
    __tablename__ = "capability_index"
    __table_args__ = (Index("ix_capability_index_version_user", "registry_version", "user_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = _user_col()
    registry_version: Mapped[int] = mapped_column(Integer, nullable=False)
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    version: Mapped[str | None] = mapped_column(String(32))
    kind: Mapped[str] = mapped_column(String(16), nullable=False)  # skill / tool / toolkit
    scope: Mapped[str] = mapped_column(String(16), nullable=False)
    owner_user_id: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    toolkit: Mapped[str | None] = mapped_column(String(64))
    audiences: Mapped[list[Any]] = mapped_column(JsonType, default=list)
    l0_text: Mapped[str] = mapped_column(Text, nullable=False, default="")
    l1_text: Mapped[str | None] = mapped_column(Text)
    triggers: Mapped[dict[str, Any]] = mapped_column(JsonType, default=dict)
    invocation: Mapped[str] = mapped_column(String(16), nullable=False, default="tool_call")
    token_counts: Mapped[dict[str, Any]] = mapped_column(JsonType, default=dict)
    embedding: Mapped[list[Any] | None] = mapped_column(JsonType)  # 预留：默认不计算


class CapabilityDisclosureRow(Base):
    __tablename__ = "capability_disclosures"
    __table_args__ = (Index("ix_capability_disclosures_run", "run_id"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = _user_col()
    run_id: Mapped[str] = mapped_column(String(96), nullable=False)
    audience: Mapped[str] = mapped_column(String(32), nullable=False)
    disclosed: Mapped[list[Any]] = mapped_column(JsonType, default=list)  # [{name, level, tokens, score}]
    used: Mapped[list[Any]] = mapped_column(JsonType, default=list)  # [name]
    total_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    context: Mapped[dict[str, Any]] = mapped_column(JsonType, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class SkillProposalRow(Base):
    """P3 保留提议，P4/P5 的事实投影器消费；不直接写未经校验的领域事实。"""

    __tablename__ = "skill_proposals"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = _user_col()
    document_id: Mapped[str | None] = mapped_column(String(64))
    artifact_id: Mapped[str] = mapped_column(String(64), ForeignKey("artifacts.id", ondelete="CASCADE"))
    run_id: Mapped[str] = mapped_column(String(96))
    kind: Mapped[str] = mapped_column(String(16))
    target_ref: Mapped[str] = mapped_column(String(256))
    payload: Mapped[dict[str, Any]] = mapped_column(JsonType)
    status: Mapped[str] = mapped_column(String(16), default="proposed")


class CompoundRow(Base):
    __tablename__ = "compounds"
    __table_args__ = (UniqueConstraint("document_id", "primary_label", name="uq_compound_label"),)
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = _user_col()
    document_id: Mapped[str] = mapped_column(String(64), ForeignKey("documents.id", ondelete="CASCADE"))
    primary_label: Mapped[str] = mapped_column(String(128))
    accepted_structure_id: Mapped[str | None] = mapped_column(String(64))
    stereo_parent_id: Mapped[str | None] = mapped_column(String(64))
    payload: Mapped[dict[str, Any]] = mapped_column(JsonType, default=dict)


class CompoundAliasRow(Base):
    __tablename__ = "compound_aliases"
    __table_args__ = (UniqueConstraint("compound_id", "alias", name="uq_compound_alias"),)
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = _user_col()
    compound_id: Mapped[str] = mapped_column(String(64), ForeignKey("compounds.id", ondelete="CASCADE"))
    alias: Mapped[str] = mapped_column(String(128))


class ChemistryFactMixin:
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = _user_col()
    document_id: Mapped[str] = mapped_column(String(64), ForeignKey("documents.id", ondelete="CASCADE"))
    artifact_id: Mapped[str] = mapped_column(String(64), ForeignKey("artifacts.id", ondelete="CASCADE"))
    target_ref: Mapped[str] = mapped_column(String(256))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JsonType, default=dict)


class StructureCandidateRow(ChemistryFactMixin, Base):
    __tablename__ = "structure_candidates"
    compound_id: Mapped[str | None] = mapped_column(String(64), ForeignKey("compounds.id"))
    source: Mapped[str] = mapped_column(String(32))
    node_ref: Mapped[str] = mapped_column(String(256))


class LabelClaimRow(ChemistryFactMixin, Base):
    __tablename__ = "label_claims"
    label: Mapped[str] = mapped_column(String(128))
    structure_id: Mapped[str | None] = mapped_column(String(64))


class MarkushScaffoldRow(ChemistryFactMixin, Base):
    __tablename__ = "markush_scaffolds"


class RGroupAssignmentRow(ChemistryFactMixin, Base):
    __tablename__ = "rgroup_assignments"


class ChemistryConflictRow(ChemistryFactMixin, Base):
    __tablename__ = "chemistry_conflicts"
    compound_id: Mapped[str] = mapped_column(String(64), ForeignKey("compounds.id", ondelete="CASCADE"))


class AssayRow(ChemistryFactMixin, Base):
    __tablename__ = "assays"


class MeasurementRow(ChemistryFactMixin, Base):
    __tablename__ = "measurements"


class FigureRelationRow(ChemistryFactMixin, Base):
    __tablename__ = "figure_relations"


class FigureReportRow(Base):
    __tablename__ = "figure_reports"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(64), nullable=False, default="default")
    document_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("documents.id", ondelete="CASCADE"), nullable=False
    )
    node_ref: Mapped[str] = mapped_column(String(256), nullable=False)
    mode: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    input_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JsonType, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    __table_args__ = (
        UniqueConstraint("user_id", "node_ref", "input_fingerprint", "mode", name="uq_figure_report_work"),
    )


class BatchRow(Base):
    __tablename__ = "batches"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(64), nullable=False, default="default")
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="queued")
    payload: Mapped[dict[str, Any]] = mapped_column(JsonType, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class LookupRow(Base):
    __tablename__ = "lookups"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = _user_col()
    document_id: Mapped[str] = mapped_column(String(64), ForeignKey("documents.id", ondelete="CASCADE"))
    revision: Mapped[int] = mapped_column(Integer)
    fingerprint: Mapped[str] = mapped_column(String(64))
    question_key: Mapped[str] = mapped_column(Text)
    run_id: Mapped[str] = mapped_column(String(96))
    payload: Mapped[dict[str, Any]] = mapped_column(JsonType)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    __table_args__ = (Index("ix_lookup_cache", "user_id", "document_id", "revision", "fingerprint"),)


class ReaderNoteRow(Base):
    __tablename__ = "reader_notes"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = _user_col()
    document_id: Mapped[str] = mapped_column(String(64), ForeignKey("documents.id", ondelete="CASCADE"))
    revision: Mapped[int] = mapped_column(Integer)
    fingerprint: Mapped[str] = mapped_column(String(64))
    page: Mapped[int] = mapped_column(Integer)
    version: Mapped[int] = mapped_column(Integer)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JsonType)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    __table_args__ = (
        UniqueConstraint("user_id", "document_id", "revision", "page", "version", name="uq_reader_version"),
    )


class FullTextResultRow(Base):
    __tablename__ = "full_text_results"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = _user_col()
    document_id: Mapped[str] = mapped_column(String(64), ForeignKey("documents.id", ondelete="CASCADE"))
    revision: Mapped[int] = mapped_column(Integer)
    fingerprint: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(32))
    payload: Mapped[dict[str, Any]] = mapped_column(JsonType)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ExportRow(Base):
    __tablename__ = "exports"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = _user_col()
    document_id: Mapped[str] = mapped_column(String(64), ForeignKey("documents.id", ondelete="CASCADE"))
    result_id: Mapped[str] = mapped_column(String(64), ForeignKey("full_text_results.id", ondelete="CASCADE"))
    blob_ref: Mapped[str] = mapped_column(String(64))
    payload: Mapped[dict[str, Any]] = mapped_column(JsonType)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
