"""second_ops

Revision ID: 0012
Revises: 0011
Create Date: 2026-08-28

Adds the 2nd OPS audit surface: one child table for the Audit BOM transcription
and two nullable columns on jobs. No data backfill is required — both new jobs
columns are nullable with no server default, and second_ops_reviewed_at IS NULL
is exactly the correct `unaudited` state for every pre-existing row.

Every job_second_ops_lines column carries an explicit width. The single
unbounded column added here is jobs.second_ops_unexpected_inclusions, which is
prose bounded by settings.second_ops_note_max_chars at validation.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "0012"
down_revision: Union[str, Sequence[str], None] = "0011"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "job_second_ops_lines",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column(
            "job_id",
            sa.Integer,
            sa.ForeignKey("jobs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("line_order", sa.Integer, nullable=False),
        sa.Column("find_number", sa.String(32), nullable=True),
        sa.Column("component_part_number", sa.String(128), nullable=True),
        sa.Column("per_board_count", sa.String(32), nullable=True),
        sa.Column("ref_des", sa.String(2048), nullable=True),
        sa.Column("description", sa.String(255), nullable=True),
        sa.Column("mount_type", sa.String(16), nullable=True),
        sa.Column("quantity_needed", sa.String(32), nullable=True),
        sa.Column("quantity_on_hand", sa.String(32), nullable=True),
        sa.Column(
            "created_at", sa.DateTime, server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at",
            sa.DateTime,
            server_default=sa.func.now(),
            onupdate=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_second_ops_job_order", "job_second_ops_lines", ["job_id", "line_order"]
    )

    # SQLite supports both ADD COLUMN forms natively; no batch mode needed.
    op.add_column("jobs", sa.Column("second_ops_reviewed_at", sa.DateTime, nullable=True))
    op.add_column(
        "jobs", sa.Column("second_ops_unexpected_inclusions", sa.Text, nullable=True)
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("jobs", "second_ops_unexpected_inclusions")
    op.drop_column("jobs", "second_ops_reviewed_at")
    op.drop_index("ix_second_ops_job_order", table_name="job_second_ops_lines")
    op.drop_table("job_second_ops_lines")
