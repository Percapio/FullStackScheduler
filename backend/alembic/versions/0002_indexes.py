"""hot-path indexes

Revision ID: 0002
Revises: 0001
Create Date: 2026-04-25

"""
from typing import Sequence, Union

from alembic import op


revision: str = "0002"
down_revision: Union[str, Sequence[str], None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("jobs", schema=None) as batch_op:
        batch_op.create_index("ix_jobs_status", ["status"], unique=False)
        batch_op.create_index("ix_jobs_customer_id", ["customer_id"], unique=False)
        batch_op.create_index("ix_jobs_assembly_id", ["assembly_id"], unique=False)
        batch_op.create_index("ix_jobs_build_type", ["build_type"], unique=False)
        batch_op.create_index("ix_jobs_shipped_at", ["shipped_at"], unique=False)
        batch_op.create_index(
            "ix_jobs_resolved_ship_date", ["resolved_ship_date"], unique=False
        )

    with op.batch_alter_table("import_staging", schema=None) as batch_op:
        batch_op.create_index(
            "ix_import_staging_processing_status",
            ["processing_status"],
            unique=False,
        )


def downgrade() -> None:
    with op.batch_alter_table("import_staging", schema=None) as batch_op:
        batch_op.drop_index("ix_import_staging_processing_status")

    with op.batch_alter_table("jobs", schema=None) as batch_op:
        batch_op.drop_index("ix_jobs_resolved_ship_date")
        batch_op.drop_index("ix_jobs_shipped_at")
        batch_op.drop_index("ix_jobs_build_type")
        batch_op.drop_index("ix_jobs_assembly_id")
        batch_op.drop_index("ix_jobs_customer_id")
        batch_op.drop_index("ix_jobs_status")
