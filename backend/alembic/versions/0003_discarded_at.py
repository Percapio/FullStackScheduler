"""discarded_at column on import_staging

Revision ID: 0003
Revises: 0002
Create Date: 2026-04-30

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "0003"
down_revision: Union[str, Sequence[str], None] = "0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("import_staging", schema=None) as batch_op:
        batch_op.add_column(sa.Column("discarded_at", sa.DateTime(), nullable=True))
        batch_op.create_index(
            "ix_import_staging_discarded_at",
            ["discarded_at"],
            unique=False,
        )


def downgrade() -> None:
    with op.batch_alter_table("import_staging", schema=None) as batch_op:
        batch_op.drop_index("ix_import_staging_discarded_at")
        batch_op.drop_column("discarded_at")
