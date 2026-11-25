"""P5 measurements, reports, relations and batch tasks."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0007_figure_agents"
down_revision = "0006_chemistry"
branch_labels = None
depends_on = None


def upgrade() -> None:
    jt = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")
    for name in ("assays", "measurements", "figure_relations"):
        op.create_table(
            name,
            sa.Column("id", sa.String(64), primary_key=True),
            sa.Column("user_id", sa.String(64), nullable=False),
            sa.Column(
                "document_id",
                sa.String(64),
                sa.ForeignKey("documents.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column(
                "artifact_id",
                sa.String(64),
                sa.ForeignKey("artifacts.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("target_ref", sa.String(256), nullable=False),
            sa.Column("is_active", sa.Boolean(), nullable=False),
            sa.Column("payload", jt, nullable=False),
        )
    op.create_table(
        "figure_reports",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("user_id", sa.String(64), nullable=False),
        sa.Column(
            "document_id", sa.String(64), sa.ForeignKey("documents.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("node_ref", sa.String(256), nullable=False),
        sa.Column("mode", sa.String(16), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("input_fingerprint", sa.String(64), nullable=False),
        sa.Column("payload", jt, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("user_id", "node_ref", "input_fingerprint", "mode", name="uq_figure_report_work"),
    )
    op.create_table(
        "batches",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("user_id", sa.String(64), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("payload", jt, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    for name in ("batches", "figure_reports", "figure_relations", "measurements", "assays"):
        op.drop_table(name)
