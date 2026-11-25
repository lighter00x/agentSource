"""能力索引与披露记录：capability_index、capability_disclosures。

Revision ID: 0004_capability_index
Revises: 0003_skills
Create Date: 2026-09-30
"""

from __future__ import annotations

from typing import Any

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0004_capability_index"
down_revision = "0003_skills"
branch_labels = None
depends_on = None


def _json() -> Any:
    return sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def _user() -> Any:
    return sa.Column("user_id", sa.String(64), nullable=False, server_default=sa.text("'default'"))


def upgrade() -> None:
    op.create_table(
        "capability_index",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        _user(),
        sa.Column("registry_version", sa.Integer, nullable=False),
        sa.Column("name", sa.String(64), nullable=False),
        sa.Column("version", sa.String(32)),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("scope", sa.String(16), nullable=False),
        sa.Column("owner_user_id", sa.String(64), nullable=False, server_default=sa.text("''")),
        sa.Column("toolkit", sa.String(64)),
        sa.Column("audiences", _json()),
        sa.Column("l0_text", sa.Text, nullable=False, server_default=""),
        sa.Column("l1_text", sa.Text),
        sa.Column("triggers", _json()),
        sa.Column("invocation", sa.String(16), nullable=False, server_default="tool_call"),
        sa.Column("token_counts", _json()),
        sa.Column("embedding", _json()),
    )
    op.create_index("ix_capability_index_version_user", "capability_index", ["registry_version", "user_id"])
    op.create_table(
        "capability_disclosures",
        sa.Column("id", sa.String(64), primary_key=True),
        _user(),
        sa.Column("run_id", sa.String(96), nullable=False),
        sa.Column("audience", sa.String(32), nullable=False),
        sa.Column("disclosed", _json()),
        sa.Column("used", _json()),
        sa.Column("total_tokens", sa.Integer, nullable=False, server_default="0"),
        sa.Column("context", _json()),
        sa.Column("created_at", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_capability_disclosures_run", "capability_disclosures", ["run_id"])


def downgrade() -> None:
    op.drop_table("capability_disclosures")
    op.drop_table("capability_index")
