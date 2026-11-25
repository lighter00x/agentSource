"""持久化 Runtime 校验后的节点、事实与关系提议。"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0005_skill_proposals"
down_revision = "0004_capability_index"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index(
        "uq_skill_active_scope",
        "skill_versions",
        ["name", "scope", "owner_user_id"],
        unique=True,
        postgresql_where=sa.text("status = 'active'"),
        sqlite_where=sa.text("status = 'active'"),
    )
    op.create_table(
        "skill_proposals",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("user_id", sa.String(64), nullable=False, server_default="default"),
        sa.Column("document_id", sa.String(64)),
        sa.Column(
            "artifact_id", sa.String(64), sa.ForeignKey("artifacts.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("run_id", sa.String(96), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("target_ref", sa.String(256), nullable=False),
        sa.Column("payload", sa.JSON().with_variant(postgresql.JSONB(), "postgresql"), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="proposed"),
    )


def downgrade() -> None:
    op.drop_table("skill_proposals")
    op.drop_index("uq_skill_active_scope", table_name="skill_versions")
