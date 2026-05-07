"""Phase 15 Epoch 3: Add jobs.discarded_at and amend ix_job_identity_active.

Revision ID: 0007
Revises: 0006
Create Date: 2026-05-06

## Steps
1. Add jobs.discarded_at (nullable DateTime, DEFAULT NULL).
2. Drop ix_job_identity_active (predicate: superseded_at IS NULL).
3. Recreate ix_job_identity_active with amended predicate:
   WHERE superseded_at IS NULL AND discarded_at IS NULL.
4. Create ix_jobs_discarded_at_null (partial index on discarded_at IS NULL
   for fast active-jobs queries).

## Rationale
Without step 3, a discarded job retains its identity slot and the next ingest
of the same identity fails with a UNIQUE constraint violation (transform.py
upsert SELECT filters discarded_at IS NULL, falls through to INSERT, INSERT
collides with the orphaned index entry).

SQLAlchemy/Alembic does not expose ALTER INDEX SET WHERE, so drop-and-recreate
is the only correct path on SQLite.
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy import text

revision: str = "0007"
down_revision: Union[str, Sequence[str], None] = "0006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Epoch 3 upgrade.

    Pre:  Schema is at migration 0006.
    Post: - jobs.discarded_at column exists, nullable DateTime.
          - ix_job_identity_active predicate includes discarded_at IS NULL.
          - ix_jobs_discarded_at_null partial index exists.
    """
    # Step 1: Add discarded_at to jobs.
    with op.batch_alter_table("jobs", schema=None) as batch:
        batch.add_column(
            sa.Column("discarded_at", sa.DateTime, nullable=True)
        )

    # Belt-and-suspenders: confirm no row has discarded_at set (column is brand new).
    conn = op.get_bind()
    count = conn.execute(
        text("SELECT COUNT(*) FROM jobs WHERE discarded_at IS NOT NULL")
    ).scalar()
    assert count == 0, f"Unexpected discarded_at rows after column creation: {count}"

    # Step 2: Drop the existing partial unique index (predicate: superseded_at IS NULL).
    op.execute(text("DROP INDEX IF EXISTS ix_job_identity_active"))

    # Step 3: Recreate with the amended predicate including discarded_at IS NULL.
    op.execute(
        text(
            "CREATE UNIQUE INDEX ix_job_identity_active "
            "ON jobs (assembly_id, build_type, split_suffix, repeat_reference, build_qualifier) "
            "WHERE superseded_at IS NULL AND discarded_at IS NULL"
        )
    )

    # Step 4: Partial index to keep active-jobs query path fast.
    op.execute(
        text(
            "CREATE INDEX ix_jobs_discarded_at_null "
            "ON jobs (discarded_at) "
            "WHERE discarded_at IS NULL"
        )
    )


def downgrade() -> None:
    """Reverse Epoch 3.

    Post: discarded_at column removed; ix_job_identity_active reverted to
          superseded_at IS NULL predicate; ix_jobs_discarded_at_null dropped.
    """
    op.execute(text("DROP INDEX IF EXISTS ix_jobs_discarded_at_null"))
    op.execute(text("DROP INDEX IF EXISTS ix_job_identity_active"))

    # Restore the Phase 14 predicate.
    op.execute(
        text(
            "CREATE UNIQUE INDEX ix_job_identity_active "
            "ON jobs (assembly_id, build_type, split_suffix, repeat_reference, build_qualifier) "
            "WHERE superseded_at IS NULL"
        )
    )

    with op.batch_alter_table("jobs", schema=None) as batch:
        batch.drop_column("discarded_at")
