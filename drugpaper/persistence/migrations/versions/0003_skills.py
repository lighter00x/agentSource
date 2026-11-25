"""Skill 注册表：skill_versions。

Revision ID: 0003_skills
Revises: 0002_text_index
Create Date: 2026-09-30
"""

from __future__ import annotations

from typing import Any

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0003_skills"
down_revision = "0002_text_index"
branch_labels = None
depends_on = None


def _json() -> Any:
    return sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    op.create_table(
        "skill_versions",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("user_id", sa.String(64), nullable=False, server_default=sa.text("'default'")),
        sa.Column("name", sa.String(64), nullable=False),
        sa.Column("version", sa.String(32), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("scope", sa.String(16), nullable=False),
        sa.Column("owner_user_id", sa.String(64), nullable=False, server_default=sa.text("''")),
        sa.Column("source", sa.String(16), nullable=False, server_default="manual"),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("path", sa.Text),
        sa.Column("manifest", _json()),
        sa.Column("test_report", _json()),
        sa.Column("errors", _json()),
        sa.Column("warnings", _json()),
        sa.Column("created_at", sa.DateTime(timezone=True)),
        sa.Column("confirmed_at", sa.DateTime(timezone=True)),
        sa.Column("activated_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint(
            "name", "version", "content_hash", "scope", "owner_user_id", name="uq_skill_versions_identity"
        ),
    )
    op.create_index("ix_skill_versions_name_status", "skill_versions", ["name", "status"])


def downgrade() -> None:
    op.drop_table("skill_versions")
