"""Phase 18a — Upload review workflow: new ImportStatus values + review columns.

Revision ID: 0009
Revises: 0008
Create Date: 2026-05-16

## Steps
1. Add 'awaiting_review' and 'abandoned' to the import_batch_status enum via
   batch_alter_table (portable across SQLite and Postgres).
2. Add 'awaiting_review' and 'abandoned' to the import_row_status enum
   (import_staging.processing_status shares the same SQLAlchemy enum object).
3. Add six review-tracking columns to import_staging; all nullable and
   populated only when a row passes through the review surface.
4. Add index ix_staging_batch_review_status on (batch_id, review_status) to
   support the review-API lookup path.

## Notes
- import_staging.discarded_at already exists (added in 0007); NOT re-added here.
- No downgrade is provided: removing enum values is destructive and not safe
  in production; callers must roll forward.
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0009"
down_revision: Union[str, Sequence[str], None] = "0008"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Phase 18a upgrade.

    Pre:  Schema is at migration 0008.
    Post: - import_batch_status and import_row_status enums include
            'awaiting_review' and 'abandoned'.
          - import_staging has six new nullable review columns.
          - ix_staging_batch_review_status index exists.
    Raises: nothing beyond normal SQLAlchemy/Alembic errors.
    """
    # SQLite does not have ALTER TYPE; SQLAlchemy's batch_alter_table
    # re-creates the table with the updated column definition, which is the
    # portable approach for both SQLite and Postgres-like engines.

    # Step 1: Extend import_batches.status enum by recreating the column.
    with op.batch_alter_table("import_batches", schema=None) as batch:
        batch.alter_column(
            "status",
            existing_type=sa.Enum(
                "pending", "processed", "error",
                name="import_batch_status",
            ),
            type_=sa.Enum(
                "pending", "processed", "error",
                "awaiting_review", "abandoned",
                name="import_batch_status",
            ),
            existing_nullable=False,
        )

    # Step 2: Extend import_staging.processing_status enum.
    with op.batch_alter_table("import_staging", schema=None) as batch:
        batch.alter_column(
            "processing_status",
            existing_type=sa.Enum(
                "pending", "processed", "error",
                name="import_row_status",
            ),
            type_=sa.Enum(
                "pending", "processed", "error",
                "awaiting_review", "abandoned",
                name="import_row_status",
            ),
            existing_nullable=False,
        )

    # Step 3: Add review-tracking columns to import_staging.
    with op.batch_alter_table("import_staging", schema=None) as batch:
        batch.add_column(
            sa.Column("original_raw_job", sa.Text(), nullable=True)
        )
        batch.add_column(
            sa.Column("review_status", sa.Text(), nullable=True)
        )
        batch.add_column(
            sa.Column("reviewed_by", sa.Text(), nullable=True)
        )
        batch.add_column(
            sa.Column("reviewed_at", sa.DateTime(), nullable=True)
        )
        batch.add_column(
            sa.Column("review_part_number_override", sa.Text(), nullable=True)
        )
        batch.add_column(
            sa.Column("review_split_suffix_override", sa.Text(), nullable=True)
        )

    # Step 4: Index for the review-API lookup path.
    op.create_index(
        "ix_staging_batch_review_status",
        "import_staging",
        ["batch_id", "review_status"],
    )


def downgrade() -> None:
    raise NotImplementedError(
        "0009 downgrade not supported: removing enum values ('awaiting_review', "
        "'abandoned') is destructive in both SQLite (table recreation) and "
        "Postgres (ALTER TYPE DROP VALUE is blocked when values are in use). "
        "Roll forward instead."
    )
