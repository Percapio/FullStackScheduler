"""duplicate_group_key column on import_staging

Revision ID: 0004
Revises: 0003
Create Date: 2026-05-02

## Assumptions
- decompose_job_string in backend.app.extractors is a pure parser with no DB or
  session dependency. Calling it from a migration is therefore safe — it cannot
  trigger implicit DB access or lazy-load side-effects.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy import text

# Import the pure parser for the best-effort backfill.
from backend.app.extractors import decompose_job_string

revision: str = "0004"
down_revision: Union[str, Sequence[str], None] = "0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("import_staging", schema=None) as batch_op:
        batch_op.add_column(sa.Column("duplicate_group_key", sa.Text(), nullable=True))
        batch_op.create_index(
            "ix_import_staging_dup_group",
            ["batch_id", "duplicate_group_key"],
            unique=False,
        )
    _backfill(op.get_bind())


def _backfill(connection) -> None:
    """Best-effort: parse processing_error for the duplicate prefix, then derive the
    canonical key from raw_job via decompose_job_string.  Rows that fail to parse stay NULL.
    Discarded rows are skipped per §3.6.2 invariant ('a discarded row is not in any group').
    """
    rows = connection.execute(text("""
        SELECT id, batch_id, raw_job, processing_error
          FROM import_staging
         WHERE processing_error LIKE 'Intra-file duplicate JOB identity %'
           AND duplicate_group_key IS NULL
           AND discarded_at IS NULL
    """))
    for r in rows:
        decomp = decompose_job_string(r.raw_job) if r.raw_job else None
        if decomp is None:
            continue  # leave NULL; row stays "ungrouped"
        key = (
            f"{decomp.part_number}|{decomp.build_type.value}"
            f"|{decomp.split_suffix or ''}|{decomp.repeat_reference or ''}"
        )
        connection.execute(
            text("UPDATE import_staging SET duplicate_group_key = :k WHERE id = :i"),
            {"k": key, "i": r.id},
        )


def downgrade() -> None:
    with op.batch_alter_table("import_staging", schema=None) as batch_op:
        batch_op.drop_index("ix_import_staging_dup_group")
        batch_op.drop_column("duplicate_group_key")
