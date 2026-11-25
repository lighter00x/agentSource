"""核心表：documents、parse_revisions、assets、nodes、relations、runs、artifacts、artifact_dependencies。

PostgreSQL 上另外创建扩展 vector / pg_trgm（可用时）与 LangGraph checkpointer 用的 schema `lg`。

Revision ID: 0001_core
Revises:
Create Date: 2026-09-30
"""

from __future__ import annotations

from typing import Any

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0001_core"
down_revision = None
branch_labels = None
depends_on = None


def _json() -> Any:
    return sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def _user() -> Any:
    return sa.Column("user_id", sa.String(64), nullable=False, server_default=sa.text("'default'"))


def _ts(name: str, nullable: bool = True) -> Any:
    return sa.Column(name, sa.DateTime(timezone=True), nullable=nullable)


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        # 扩展按可用性创建：嵌入式 PostgreSQL（测试用）可能没有 pg_trgm
        op.execute(
            """
            DO $$
            BEGIN
              IF EXISTS (SELECT 1 FROM pg_available_extensions WHERE name = 'vector') THEN
                CREATE EXTENSION IF NOT EXISTS vector;
              END IF;
              IF EXISTS (SELECT 1 FROM pg_available_extensions WHERE name = 'pg_trgm') THEN
                CREATE EXTENSION IF NOT EXISTS pg_trgm;
              END IF;
            END $$;
            """
        )
        op.execute("CREATE SCHEMA IF NOT EXISTS lg")

    op.create_table(
        "documents",
        sa.Column("id", sa.String(64), primary_key=True),
        _user(),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("filename", sa.Text),
        sa.Column("title", sa.Text),
        sa.Column("doi", sa.Text),
        sa.Column("status", sa.String(32), nullable=False, server_default="uploaded"),
        sa.Column("active_revision", sa.Integer),
        sa.Column("batch_id", sa.String(64)),
        sa.Column("config", _json()),
        sa.Column("meta", _json()),
        sa.Column("quality_report", _json()),
        sa.Column("error", _json()),
        _ts("created_at"),
        _ts("updated_at"),
        _ts("deleted_at"),
        sa.UniqueConstraint("user_id", "sha256", name="uq_documents_user_sha256"),
    )
    op.create_table(
        "parse_revisions",
        sa.Column(
            "document_id", sa.String(64), sa.ForeignKey("documents.id", ondelete="CASCADE"), primary_key=True
        ),
        sa.Column("revision", sa.Integer, primary_key=True),
        _user(),
        sa.Column("parser", sa.String(64), nullable=False),
        sa.Column("parser_version", sa.String(32), nullable=False),
        sa.Column("raw_blob_refs", _json()),
        sa.Column("status", sa.String(32), nullable=False, server_default="created"),
        _ts("created_at"),
    )
    op.create_table(
        "assets",
        sa.Column("id", sa.String(64), primary_key=True),
        _user(),
        sa.Column(
            "document_id", sa.String(64), sa.ForeignKey("documents.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("blob_ref", sa.String(64), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("content_type", sa.String(128)),
        sa.Column("path", sa.Text),
        sa.Column("meta", _json()),
        _ts("created_at"),
    )
    op.create_index("ix_assets_document_kind", "assets", ["document_id", "kind"])
    op.create_table(
        "nodes",
        sa.Column(
            "document_id", sa.String(64), sa.ForeignKey("documents.id", ondelete="CASCADE"), primary_key=True
        ),
        sa.Column("revision", sa.Integer, primary_key=True),
        sa.Column("local_id", sa.String(128), primary_key=True),
        _user(),
        sa.Column("node_type", sa.String(32), nullable=False),
        sa.Column("subtype", sa.String(64)),
        sa.Column("parent_local_id", sa.String(128)),
        sa.Column("children", _json()),
        sa.Column("reading_order", sa.Integer, nullable=False, server_default="0"),
        sa.Column("page", sa.Integer),
        sa.Column("geometry", _json()),
        sa.Column("label", sa.Text),
        sa.Column("text_raw", sa.Text),
        sa.Column("text_norm", sa.Text),
        sa.Column("section_role", sa.String(64)),
        sa.Column("source", sa.String(16), nullable=False, server_default="mineru"),
        sa.Column("source_ref", _json()),
        sa.Column("quality_flags", _json()),
        sa.Column("status", sa.String(16), nullable=False, server_default="active"),
        sa.Column("attributes", _json()),
    )
    op.create_index("ix_nodes_doc_rev_type", "nodes", ["document_id", "revision", "node_type"])
    op.create_index("ix_nodes_doc_rev_page", "nodes", ["document_id", "revision", "page"])
    op.create_table(
        "relations",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        _user(),
        sa.Column(
            "document_id", sa.String(64), sa.ForeignKey("documents.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("revision", sa.Integer, nullable=False),
        sa.Column("source", sa.String(128), nullable=False),
        sa.Column("target", sa.String(128), nullable=False),
        sa.Column("type", sa.String(32), nullable=False),
        sa.Column("evidence", _json()),
        sa.Column("status", sa.String(16), nullable=False, server_default="active"),
    )
    op.create_index("ix_relations_doc_rev", "relations", ["document_id", "revision"])
    op.create_table(
        "runs",
        sa.Column("id", sa.String(96), primary_key=True),
        _user(),
        sa.Column("document_id", sa.String(64)),
        sa.Column("run_type", sa.String(32), nullable=False),
        sa.Column("component", sa.String(128), nullable=False),
        sa.Column("component_version", sa.String(64)),
        sa.Column("component_hash", sa.String(64)),
        sa.Column("inputs", _json()),
        sa.Column("outputs", _json()),
        sa.Column("model", _json()),
        sa.Column("parent_run_id", sa.String(96)),
        sa.Column("trace_id", sa.String(96)),
        sa.Column("status", sa.String(32), nullable=False, server_default="running"),
        sa.Column("error", _json()),
        sa.Column("metrics", _json()),
        _ts("started_at"),
        _ts("ended_at"),
    )
    op.create_index("ix_runs_parent", "runs", ["parent_run_id"])
    op.create_index("ix_runs_trace", "runs", ["trace_id"])
    op.create_table(
        "artifacts",
        sa.Column("id", sa.String(64), primary_key=True),
        _user(),
        sa.Column(
            "document_id", sa.String(64), sa.ForeignKey("documents.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("artifact_type", sa.String(64), nullable=False),
        sa.Column("target_ref", sa.String(256), nullable=False),
        sa.Column("work_unit_key", sa.String(256), nullable=False),
        sa.Column("version", sa.Integer, nullable=False, server_default="1"),
        sa.Column("schema_name", sa.String(128)),
        sa.Column("schema_version", sa.String(32)),
        sa.Column("payload", _json()),
        sa.Column("blob_ref", sa.String(64)),
        sa.Column("input_fingerprint", sa.String(64)),
        sa.Column("run_id", sa.String(96)),
        sa.Column("status", sa.String(32), nullable=False, server_default="completed"),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("confidence", sa.Float),
        _ts("created_at"),
    )
    op.create_index(
        "uq_artifacts_active_work_unit",
        "artifacts",
        ["work_unit_key"],
        unique=True,
        postgresql_where=sa.text("is_active"),
        sqlite_where=sa.text("is_active"),
    )
    op.create_index("ix_artifacts_doc_target", "artifacts", ["document_id", "target_ref"])
    op.create_table(
        "artifact_dependencies",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        _user(),
        sa.Column("upstream", sa.String(256), nullable=False),
        sa.Column("downstream", sa.String(64), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False, server_default="input"),
    )
    op.create_index("ix_artifact_deps_upstream", "artifact_dependencies", ["upstream"])
    op.create_index("ix_artifact_deps_downstream", "artifact_dependencies", ["downstream"])


def downgrade() -> None:
    for t in [
        "artifact_dependencies",
        "artifacts",
        "runs",
        "relations",
        "nodes",
        "assets",
        "parse_revisions",
        "documents",
    ]:
        op.drop_table(t)
    if op.get_bind().dialect.name == "postgresql":
        op.execute("DROP SCHEMA IF EXISTS lg CASCADE")
