"""Phase 4: reclassify RWK as a build qualifier; drop rwk from BuildType.

Revision ID: 0005
Revises: 0004
Create Date: 2026-05-02

## Steps
1. Add the build_qualifier column on both tables.
2. Relabel pre-existing jobs with build_type='rwk' to (build_type='new', build_qualifier='rwk').
3. Reform uq_job_identity to the 5-tuple.
4. Rebuild build_type with the post-migration enum {new, ronc, rowc}.
5. Backfill errored staging rows whose processing_error matches 'Invalid JOB cell:%'
   by re-running transform_staging_row with the updated extractor.
6. Strict-decrease verification gate: the count of 'Invalid JOB cell:%' errors must
   have dropped; if it hasn't (and there were rows to fix), the migration aborts.

## Assumptions
- transform_staging_row is safe to call inside op.get_bind()'s session — it does not
  open a separate session and its side-effects (upsert customer/assembly, create Job)
  are all within the same transaction.
- SQLite is the target engine.  All schema changes use op.batch_alter_table.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy import Enum, text
from sqlalchemy.orm import Session

from backend.app.models import ImportStagingRow, ImportStatus
from backend.app.transform import transform_staging_row

revision: str = "0005"
down_revision: Union[str, Sequence[str], None] = "0004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Phase 4 / migration 0005: reclassify RWK as a build qualifier.

    Pre:  schema is at 0004; jobs.build_type CHECK accepts {new, ronc, rowc, rwk}.
    Post: jobs.build_type CHECK accepts {new, ronc, rowc};
          jobs and import_staging gain a nullable build_qualifier column;
          jobs(uq_job_identity) is over the 5-tuple;
          all jobs with build_type='rwk' are relabelled to ('new', 'rwk');
          all import_staging rows with processing_error LIKE 'Invalid JOB cell:%'
          have been re-run through transform_staging_row;
          the count of those rows has strictly decreased (when pre-count > 0).
    Raises: RuntimeError if the strict-decrease invariant fails (pre-count > 0).
    """
    # Step 1: add the build_qualifier column on both tables.
    with op.batch_alter_table("jobs", schema=None) as batch:
        batch.add_column(
            sa.Column(
                "build_qualifier",
                Enum("rwk", "rework", "rma", name="build_qualifier"),
                nullable=True,
            )
        )
    with op.batch_alter_table("import_staging", schema=None) as batch:
        batch.add_column(
            sa.Column(
                "build_qualifier",
                Enum("rwk", "rework", "rma", name="build_qualifier"),
                nullable=True,
            )
        )

    # Step 2: relabel pre-existing rwk Jobs.
    op.execute("UPDATE jobs SET build_qualifier='rwk', build_type='new' WHERE build_type='rwk'")

    # Step 3: reform uq_job_identity to the 5-tuple.
    with op.batch_alter_table("jobs", schema=None) as batch:
        batch.drop_constraint("uq_job_identity", type_="unique")
        batch.create_unique_constraint(
            "uq_job_identity",
            ["assembly_id", "build_type", "split_suffix", "repeat_reference", "build_qualifier"],
        )

    # Step 4: rebuild build_type with the post-migration enum {new, ronc, rowc}.
    with op.batch_alter_table("jobs", schema=None) as batch:
        batch.alter_column(
            "build_type",
            existing_type=Enum("new", "ronc", "rowc", "rwk", name="build_type"),
            type_=Enum("new", "ronc", "rowc", name="build_type"),
            existing_nullable=True,
        )

    # Step 5: backfill errored staging rows by re-running the updated extractor.
    bind = op.get_bind()
    session = Session(bind=bind)

    pre_count: int = session.scalar(
        text(
            "SELECT COUNT(*) FROM import_staging "
            "WHERE processing_status='error' AND processing_error LIKE 'Invalid JOB cell:%%'"
        )
    ) or 0

    errored_rows: list[ImportStagingRow] = list(
        session.scalars(
            sa.select(ImportStagingRow).where(
                ImportStagingRow.processing_status == ImportStatus.error,
                ImportStagingRow.processing_error.like("Invalid JOB cell:%"),
            )
        ).all()
    )
    for row in errored_rows:
        transform_staging_row(session, row)
    session.flush()

    post_count: int = session.scalar(
        text(
            "SELECT COUNT(*) FROM import_staging "
            "WHERE processing_status='error' AND processing_error LIKE 'Invalid JOB cell:%%'"
        )
    ) or 0

    # Step 6: strict-decrease verification gate (TDD §3.1.5).
    # Only fires when there were rows to fix; a fresh DB with zero errored rows passes.
    if pre_count > 0 and not (post_count < pre_count):
        raise RuntimeError(
            f"Migration 0005 backfill failed strict-decrease invariant: "
            f"pre={pre_count}, post={post_count}. Expected post < pre. Aborting."
        )


def downgrade() -> None:
    """Reverse migration 0005. Restores the rwk BuildType value and drops build_qualifier.

    Pre:  schema is at 0005.
    Post: schema is at 0004; rows with build_qualifier='rwk' have build_type='rwk'
          and build_qualifier dropped; uq_job_identity is the 4-tuple.
    """
    op.execute("UPDATE jobs SET build_type='rwk' WHERE build_qualifier='rwk'")

    with op.batch_alter_table("jobs", schema=None) as batch:
        batch.alter_column(
            "build_type",
            existing_type=Enum("new", "ronc", "rowc", name="build_type"),
            type_=Enum("new", "ronc", "rowc", "rwk", name="build_type"),
            existing_nullable=True,
        )
        batch.drop_constraint("uq_job_identity", type_="unique")
        batch.create_unique_constraint(
            "uq_job_identity",
            ["assembly_id", "build_type", "split_suffix", "repeat_reference"],
        )
        batch.drop_column("build_qualifier")

    with op.batch_alter_table("import_staging", schema=None) as batch:
        batch.drop_column("build_qualifier")
