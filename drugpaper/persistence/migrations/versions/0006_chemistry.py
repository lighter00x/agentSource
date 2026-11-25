"""P4 化学事实与化合物台账（0005 已用于 Skill 提议）。"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0006_chemistry"
down_revision = "0005_skill_proposals"
branch_labels = None
depends_on = None

FACT_TABLES = (
    "structure_candidates",
    "label_claims",
    "markush_scaffolds",
    "rgroup_assignments",
    "chemistry_conflicts",
)


def upgrade() -> None:
    json_type = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")
    op.create_table(
        "compounds",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("user_id", sa.String(64), nullable=False, server_default="default"),
        sa.Column(
            "document_id", sa.String(64), sa.ForeignKey("documents.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("primary_label", sa.String(128), nullable=False),
        sa.Column("accepted_structure_id", sa.String(64)),
        sa.Column("stereo_parent_id", sa.String(64)),
        sa.Column("payload", json_type, nullable=False),
        sa.UniqueConstraint("document_id", "primary_label", name="uq_compound_label"),
    )
    op.create_table(
        "compound_aliases",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("user_id", sa.String(64), nullable=False, server_default="default"),
        sa.Column(
            "compound_id", sa.String(64), sa.ForeignKey("compounds.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("alias", sa.String(128), nullable=False),
        sa.UniqueConstraint("compound_id", "alias", name="uq_compound_alias"),
    )
    for name in FACT_TABLES:
        extra = []
        if name == "structure_candidates":
            extra = [
                sa.Column("compound_id", sa.String(64), sa.ForeignKey("compounds.id")),
                sa.Column("source", sa.String(32), nullable=False),
                sa.Column("node_ref", sa.String(256), nullable=False),
            ]
        elif name == "label_claims":
            extra = [
                sa.Column("label", sa.String(128), nullable=False),
                sa.Column("structure_id", sa.String(64)),
            ]
        elif name == "chemistry_conflicts":
            extra = [
                sa.Column(
                    "compound_id",
                    sa.String(64),
                    sa.ForeignKey("compounds.id", ondelete="CASCADE"),
                    nullable=False,
                )
            ]
        op.create_table(
            name,
            sa.Column("id", sa.String(64), primary_key=True),
            sa.Column("user_id", sa.String(64), nullable=False, server_default="default"),
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
            sa.Column("payload", json_type, nullable=False),
            *extra,
        )


def downgrade() -> None:
    for name in reversed(FACT_TABLES):
        op.drop_table(name)
    op.drop_table("compound_aliases")
    op.drop_table("compounds")
