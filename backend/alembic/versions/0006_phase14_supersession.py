"""Phase 14 Epoch 1: Sheet-aware supersession schema.

Revision ID: 0006
Revises: 0005
Create Date: 2026-05-05

## Steps
1. Add import_batches.sheet_kind (NOT NULL, server_default='live').
2. Add jobs.superseded_at and jobs.superseded_by_batch_id (both nullable).
3. Drop the uq_job_identity unique constraint.
4. Create the partial unique index ix_job_identity_active
   (WHERE superseded_at IS NULL).
5. Create the job_supersession_candidate table with its indexes.

## Assumptions
- Database is wiped before this migration runs (operator brief).
  The server_default='live' on sheet_kind is a post-wipe defence only;
  no live row backfill is performed.
- SQLite is the target engine; batch_alter_table is used for schema changes.
- The DROP constraint / CREATE partial index in step 3-4 must be a net-neutral
  change on an empty DB (no identity-collision rows to worry about).
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy import text

revision: str = "0006"
down_revision: Union[str, Sequence[str], None] = "0005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Phase 14 Epoch 1 upgrade.

    Pre:  Schema is at migration 0005.
    Post: - import_batches.sheet_kind exists, NOT NULL, server_default='live'.
          - jobs.superseded_at exists, nullable.
          - jobs.superseded_by_batch_id exists, nullable FK to import_batches.
          - jobs.uq_job_identity constraint dropped.
          - ix_job_identity_active partial unique index created on jobs.
          - job_supersession_candidate table exists with
            ix_candidate_pending_unique (partial unique on job_id WHERE resolved_at IS NULL),
            ix_candidate_resolved_at, ix_candidate_detected_batch.
    """
    # Step 1: Add sheet_kind to import_batches.
    with op.batch_alter_table("import_batches", schema=None) as batch:
        batch.add_column(
            sa.Column(
                "sheet_kind",
                sa.Enum("live", "historical", name="sheet_kind"),
                nullable=False,
                server_default="live",
            )
        )

    # Step 2: Add superseded_at and superseded_by_batch_id to jobs.
    with op.batch_alter_table("jobs", schema=None) as batch:
        batch.add_column(
            sa.Column("superseded_at", sa.DateTime, nullable=True)
        )
        # Plain Integer — SQLite does not enforce FK constraints at the DDL
        # level; the ORM relationship in models.py carries the semantic link.
        batch.add_column(
            sa.Column("superseded_by_batch_id", sa.Integer, nullable=True)
        )

    # Step 3: Drop the old uq_job_identity unique constraint.
    # Step 4: Create the partial unique index ix_job_identity_active.
    # SQLite does not support DROP CONSTRAINT in batch mode natively for named
    # unique constraints — batch_alter_table recreates the table.
    with op.batch_alter_table("jobs", schema=None) as batch:
        batch.drop_constraint("uq_job_identity", type_="unique")

    # Create the partial unique index outside of batch (SQLite CREATE INDEX supports WHERE).
    op.execute(
        text(
            "CREATE UNIQUE INDEX ix_job_identity_active "
            "ON jobs (assembly_id, build_type, split_suffix, repeat_reference, build_qualifier) "
            "WHERE superseded_at IS NULL"
        )
    )

    # Step 5: Create job_supersession_candidate table.
    op.create_table(
        "job_supersession_candidate",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column(
            "job_id",
            sa.Integer,
            sa.ForeignKey("jobs.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "detected_in_batch_id",
            sa.Integer,
            sa.ForeignKey("import_batches.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "reason",
            sa.Enum(
                "orphan_after_split",
                "orphan_after_recombine",
                "orphan_other",
                name="candidate_reason",
            ),
            nullable=False,
        ),
        sa.Column("detected_at", sa.DateTime, nullable=False),
        sa.Column("resolved_at", sa.DateTime, nullable=True),
        sa.Column(
            "resolution",
            sa.Enum("approve", "reject", "auto_returned", name="candidate_resolution"),
            nullable=True,
        ),
        sa.Column("closed_by_shield_reason", sa.String(64), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime,
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime,
            server_default=sa.func.now(),
            onupdate=sa.func.now(),
            nullable=False,
        ),
    )

    # Partial unique index: at most one pending candidate per job.
    op.execute(
        text(
            "CREATE UNIQUE INDEX ix_candidate_pending_unique "
            "ON job_supersession_candidate (job_id) "
            "WHERE resolved_at IS NULL"
        )
    )
    op.create_index(
        "ix_candidate_resolved_at",
        "job_supersession_candidate",
        ["resolved_at"],
    )
    op.create_index(
        "ix_candidate_detected_batch",
        "job_supersession_candidate",
        ["detected_in_batch_id"],
    )


def downgrade() -> None:
    """Phase 14 Epoch 1 downgrade.

    Reverses all steps in the upgrade.
    """
    op.drop_table("job_supersession_candidate")

    op.execute(text("DROP INDEX IF EXISTS ix_job_identity_active"))

    with op.batch_alter_table("jobs", schema=None) as batch:
        batch.create_unique_constraint(
            "uq_job_identity",
            ["assembly_id", "build_type", "split_suffix", "repeat_reference", "build_qualifier"],
        )
        batch.drop_column("superseded_by_batch_id")
        batch.drop_column("superseded_at")

    with op.batch_alter_table("import_batches", schema=None) as batch:
        batch.drop_column("sheet_kind")
