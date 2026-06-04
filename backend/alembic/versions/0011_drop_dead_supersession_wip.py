"""drop_dead_supersession_wip

Revision ID: 0011
Revises: 1ccdbd14008f
Create Date: 2026-06-03 23:25:55.018078

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0011'
down_revision: Union[str, Sequence[str], None] = '1ccdbd14008f'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema.
    Note: Existing candidate rows are discarded. Downgrade recreates an empty table.
    """
    conn = op.get_bind()
    wip_count = conn.scalar(sa.text("SELECT COUNT(*) FROM jobs WHERE status = 'wip'"))
    if wip_count > 0:
        raise RuntimeError(
            f"Migration 0011 aborted: {wip_count} job(s) have status='wip'. "
            "WIP is being removed; an undocumented writer exists — investigate "
            "before re-running."
        )

    sbb_count = conn.scalar(sa.text("SELECT COUNT(*) FROM jobs WHERE superseded_by_batch_id IS NOT NULL"))
    if sbb_count > 0:
        raise RuntimeError(
            f"Migration 0011 aborted: {sbb_count} job(s) have superseded_by_batch_id "
            "set; dropping the column would lose data. Investigate before re-running."
        )

    # --- 1. Drop the candidate table ---
    op.drop_table("job_supersession_candidate")

    # --- 2. Rebuild jobs ---
    with op.batch_alter_table("jobs", recreate="always") as batch:
        batch.drop_column("wip_status_note")
        batch.drop_column("wip_expected_clear_date")
        batch.drop_column("superseded_by_batch_id")
        batch.alter_column(
            "status",
            existing_type=sa.Enum("planned", "wip", "shipped", name="job_status"),
            type_=sa.Enum("planned", "shipped", name="job_status"),
            existing_nullable=False,
        )

    # --- 3. Re-establish partial indexes ---
    op.execute("DROP INDEX IF EXISTS ix_job_identity_active")
    op.execute(
        "CREATE UNIQUE INDEX ix_job_identity_active ON jobs "
        "(assembly_id, build_type, split_suffix, repeat_reference, build_qualifier) "
        "WHERE superseded_at IS NULL AND discarded_at IS NULL"
    )
    op.execute("DROP INDEX IF EXISTS ix_job_active_planned")
    op.execute(
        "CREATE INDEX ix_job_active_planned ON jobs (status) "
        "WHERE superseded_at IS NULL AND discarded_at IS NULL"
    )


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table("jobs", recreate="always") as batch:
        batch.alter_column(
            "status",
            existing_type=sa.Enum("planned", "shipped", name="job_status"),
            type_=sa.Enum("planned", "wip", "shipped", name="job_status"),
            existing_nullable=False,
        )
        batch.add_column(sa.Column("wip_status_note", sa.Text(), nullable=True))
        batch.add_column(sa.Column("wip_expected_clear_date", sa.Date(), nullable=True))
        batch.add_column(sa.Column(
            "superseded_by_batch_id", sa.Integer,
            sa.ForeignKey("import_batches.id", ondelete="RESTRICT", name="fk_jobs_superseded_by_batch_id"), nullable=True))

    op.execute("DROP INDEX IF EXISTS ix_job_identity_active")
    op.execute(
        "CREATE UNIQUE INDEX ix_job_identity_active ON jobs "
        "(assembly_id, build_type, split_suffix, repeat_reference, build_qualifier) "
        "WHERE superseded_at IS NULL AND discarded_at IS NULL"
    )
    op.execute("DROP INDEX IF EXISTS ix_job_active_planned")
    op.execute(
        "CREATE INDEX ix_job_active_planned ON jobs (status) "
        "WHERE superseded_at IS NULL AND discarded_at IS NULL"
    )

    op.create_table(
        "job_supersession_candidate",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("job_id", sa.Integer,
                  sa.ForeignKey("jobs.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("detected_in_batch_id", sa.Integer,
                  sa.ForeignKey("import_batches.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("reason", sa.Enum("orphan_after_split", "orphan_after_recombine",
                  "orphan_other", name="candidate_reason"), nullable=False),
        sa.Column("detected_at", sa.DateTime, nullable=False),
        sa.Column("resolved_at", sa.DateTime, nullable=True),
        sa.Column("resolution", sa.Enum("approve", "reject", "auto_returned",
                  name="candidate_resolution"), nullable=True),
        sa.Column("closed_by_shield_reason", sa.String(64), nullable=True),
        sa.Column("created_at", sa.DateTime, server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime, server_default=sa.func.now(),
                  onupdate=sa.func.now(), nullable=False),
    )
    op.execute("CREATE UNIQUE INDEX ix_candidate_pending_unique "
               "ON job_supersession_candidate (job_id) WHERE resolved_at IS NULL")
    op.create_index("ix_candidate_resolved_at", "job_supersession_candidate", ["resolved_at"])
    op.create_index("ix_candidate_detected_batch", "job_supersession_candidate",
                    ["detected_in_batch_id"])
