"""Phase 18a Patch 02 — Add parsed_part_number column to import_staging.

Revision ID: 0010
Revises: 0009
Create Date: 2026-05-17

## Steps
1. Add nullable TEXT column parsed_part_number to import_staging.
2. Create composite index ix_staging_batch_parsed_pn on (batch_id, parsed_part_number)
   to support the indexed read path in _rows_for_pn.

## Notes
- Forward-only: downgrade raises NotImplementedError to prevent accidental rollback.
- Pre-0010 rows have parsed_part_number IS NULL; the API falls back to Python-side
  decomposition for those rows (back-compat path in _rows_for_pn_via_decomposition).
"""

import sqlalchemy as sa
from alembic import op

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("import_staging") as batch_op:
        batch_op.add_column(sa.Column("parsed_part_number", sa.Text(), nullable=True))

    op.create_index(
        "ix_staging_batch_parsed_pn",
        "import_staging",
        ["batch_id", "parsed_part_number"],
    )


def downgrade() -> None:
    raise NotImplementedError("Migration 0010 is forward-only")
