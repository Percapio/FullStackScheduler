"""Phase 16: Add jobs.ever_shipped_at column.

Revision ID: 0008
Revises: 0007
Create Date: 2026-05-09

## Steps
1. Pre-upgrade INV-S2 validation: abort if any row has shipped_at IS NOT NULL
   AND status != 'shipped'.
2. Add jobs.ever_shipped_at (nullable Date, no default, no server_default).
3. Backfill ever_shipped_at = shipped_at WHERE shipped_at IS NOT NULL
   AND ever_shipped_at IS NULL.
4. Create partial index ix_jobs_ever_shipped_at (WHERE ever_shipped_at IS NOT NULL).

## Rationale
ever_shipped_at is the new shield-truth for "this job has shipped at least once."
shipped_at may now be cleared on un-ship (Phase 16); ever_shipped_at is monotonic
and never cleared.  The backfill is idempotent: the WHERE ever_shipped_at IS NULL
guard prevents overwriting values set by a prior partial run.
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy import text

revision: str = "0008"
down_revision: Union[str, Sequence[str], None] = "0007"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Phase 16 upgrade.

    Pre:  Schema is at migration 0007.
    Post: - jobs.ever_shipped_at column exists, nullable Date.
          - INV-S2 has been validated (no row with shipped_at set and
            status != 'shipped').
          - Backfill applied: every shipped row has ever_shipped_at = shipped_at.
          - Partial index ix_jobs_ever_shipped_at exists (WHERE IS NOT NULL).
    Raises: RuntimeError when the INV-S2 validation finds violating rows.
    """
    conn = op.get_bind()

    # Step 1: INV-S2 validation — abort if any row has shipped_at set on a
    # non-shipped job.  Such a row would be permanently shielded from supersession
    # after the code switch, masking an underlying data integrity defect.
    violating_row = conn.execute(
        text(
            "SELECT id, status, shipped_at "
            "FROM jobs "
            "WHERE shipped_at IS NOT NULL "
            "  AND status <> 'shipped' "
            "LIMIT 1"
        )
    ).fetchone()

    if violating_row is not None:
        raise RuntimeError(
            f"Migration 0008 aborted: job id={violating_row.id} has "
            f"shipped_at={violating_row.shipped_at!r} but status={violating_row.status!r}. "
            f"Reconcile this row manually before re-running the migration."
        )

    # Step 2: Add the ever_shipped_at column.
    with op.batch_alter_table("jobs", schema=None) as batch:
        batch.add_column(
            sa.Column("ever_shipped_at", sa.Date(), nullable=True)
        )

    # Step 3: Backfill.  The AND ever_shipped_at IS NULL guard makes this safe
    # to re-execute against a partially-migrated database without overwriting
    # values that a prior run already set (preserves INV-S3).
    conn.execute(
        text(
            "UPDATE jobs "
            "SET ever_shipped_at = shipped_at "
            "WHERE shipped_at IS NOT NULL "
            "  AND ever_shipped_at IS NULL"
        )
    )

    # Step 4: Partial index on the non-null population — used by the
    # supersession shield's hot path.
    op.execute(
        text(
            "CREATE INDEX ix_jobs_ever_shipped_at "
            "ON jobs (ever_shipped_at) "
            "WHERE ever_shipped_at IS NOT NULL"
        )
    )


def downgrade() -> None:
    """Reverse Phase 16 upgrade.

    Post: ix_jobs_ever_shipped_at dropped; ever_shipped_at column dropped.
          Application-level shield reverts to shipped_at; downgrade must be
          paired with a code rollback (operational note).
    """
    op.execute(text("DROP INDEX IF EXISTS ix_jobs_ever_shipped_at"))

    with op.batch_alter_table("jobs", schema=None) as batch:
        batch.drop_column("ever_shipped_at")
