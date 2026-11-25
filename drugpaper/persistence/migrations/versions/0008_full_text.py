"""P6 durable lookups, reader checkpoints, projections and export packages."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import SchemaItem

revision = "0008_full_text"
down_revision = "0007_figure_agents"
branch_labels = None
depends_on = None


def upgrade() -> None:
    jt = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")
    for name in ("lookups", "reader_notes", "full_text_results", "exports"):
        columns: list[SchemaItem] = [
            sa.Column("id", sa.String(64), primary_key=True),
            sa.Column("user_id", sa.String(64), nullable=False),
            sa.Column(
                "document_id",
                sa.String(64),
                sa.ForeignKey("documents.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("payload", jt, nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        ]
        if name != "exports":
            columns.extend(
                [
                    sa.Column("revision", sa.Integer(), nullable=False),
                    sa.Column("fingerprint", sa.String(64), nullable=False),
                ]
            )
        if name == "lookups":
            columns.extend(
                [
                    sa.Column("question_key", sa.Text(), nullable=False),
                    sa.Column("run_id", sa.String(96), nullable=False),
                ]
            )
        if name == "reader_notes":
            columns.extend(
                [
                    sa.Column("page", sa.Integer(), nullable=False),
                    sa.Column("version", sa.Integer(), nullable=False),
                    sa.Column("is_active", sa.Boolean(), nullable=False),
                ]
            )
        if name == "full_text_results":
            columns.append(sa.Column("status", sa.String(32), nullable=False))
        if name == "exports":
            columns.extend(
                [
                    sa.Column(
                        "result_id",
                        sa.String(64),
                        sa.ForeignKey("full_text_results.id", ondelete="CASCADE"),
                        nullable=False,
                    ),
                    sa.Column("blob_ref", sa.String(64), nullable=False),
                ]
            )
        if name == "reader_notes":
            op.create_table(
                name,
                *columns,
                sa.UniqueConstraint(
                    "user_id", "document_id", "revision", "page", "version", name="uq_reader_version"
                ),
            )
        else:
            op.create_table(name, *columns)
    op.create_index("ix_lookup_cache", "lookups", ["user_id", "document_id", "revision", "fingerprint"])


def downgrade() -> None:
    for name in ("exports", "full_text_results", "reader_notes", "lookups"):
        op.drop_table(name)
