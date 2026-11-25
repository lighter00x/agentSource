"""全文检索索引（PostgreSQL）：nodes.tsv + GIN；text_norm 上的 pg_trgm 索引（扩展可用时）。

SQLite 上不做任何事（检索退回 Python 实现）。

Revision ID: 0002_text_index
Revises: 0001_core
Create Date: 2026-09-30
"""

from __future__ import annotations

from alembic import op

revision = "0002_text_index"
down_revision = "0001_core"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    op.execute("ALTER TABLE nodes ADD COLUMN IF NOT EXISTS tsv tsvector")
    op.execute("CREATE INDEX IF NOT EXISTS ix_nodes_tsv ON nodes USING gin (tsv)")
    op.execute(
        """
        DO $$
        BEGIN
          IF EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'pg_trgm') THEN
            CREATE INDEX IF NOT EXISTS ix_nodes_text_norm_trgm ON nodes USING gin (text_norm gin_trgm_ops);
          END IF;
        END $$;
        """
    )


def downgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    op.execute("DROP INDEX IF EXISTS ix_nodes_text_norm_trgm")
    op.execute("DROP INDEX IF EXISTS ix_nodes_tsv")
    op.execute("ALTER TABLE nodes DROP COLUMN IF EXISTS tsv")
