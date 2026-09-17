"""Patch 06 — Add split-suffix provenance to import_staging.

Revision ID: 0013
Revises: 0012
Create Date: 2026-09-16

## Steps
1. Add nullable TEXT column review_split_suffix_source to import_staging with
   CHECK (review_split_suffix_source IN ('computed', 'operator')).
2. Backfill every row with a non-null review_part_number_override:
   'computed' when its review_split_suffix_override equals the suffix
   PUT /canonical would compute today, 'operator' otherwise.  Rows without a
   part-number override keep NULL, establishing the invariant
   review_split_suffix_source IS NULL <=> review_part_number_override IS NULL.

## Notes
- _compute_split_suffix is copied from backend/app/api/ingest.py, not imported:
  a migration must not depend on application code that may change later.
- Backfill ambiguity (accepted): an operator suffix that happens to equal the
  computed one is classified 'computed', so a later canonical change recomputes
  it.  That is the pre-0013 behaviour for every row.
- Downgrade drops the column.  Provenance is lost; pre-0013 code recomputes
  every suffix on PUT /canonical, which is the pre-patch behaviour.
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0013"
down_revision: Union[str, Sequence[str], None] = "0012"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_CHECK_NAME = "ck_import_staging_split_suffix_source"


def _compute_split_suffix(original: str, canonical: str) -> str | None:
    """Frozen copy of backend.app.api.ingest._compute_split_suffix as of 0013.

    Pre:  original is the raw cell text; canonical is the part-number override.
    Post: the remainder of original's first line after canonical when canonical
          is a prefix of it (None when the remainder is empty); None otherwise.
    Raises: never.
    """
    first_line = original.split("\n")[0]
    if first_line.startswith(canonical):
        remainder = first_line[len(canonical):]
        return remainder or None
    return None


def upgrade() -> None:
    """Patch 06 upgrade.

    Pre:  Schema is at migration 0012.
    Post: import_staging.review_split_suffix_source exists with its CHECK
          constraint; every row with a part-number override carries
          'computed' or 'operator'; every other row carries NULL.
    Raises: nothing beyond normal SQLAlchemy/Alembic errors.
    """
    with op.batch_alter_table("import_staging", schema=None) as batch:
        batch.add_column(
            sa.Column("review_split_suffix_source", sa.Text(), nullable=True)
        )
        batch.create_check_constraint(
            _CHECK_NAME,
            "review_split_suffix_source IN ('computed', 'operator')",
        )

    bind = op.get_bind()
    overridden = bind.execute(sa.text(
        "SELECT id, original_raw_job, raw_job, review_part_number_override, "
        "review_split_suffix_override "
        "FROM import_staging WHERE review_part_number_override IS NOT NULL"
    )).fetchall()

    assignments: list[dict[str, object]] = []
    for staged in overridden:
        original: str = staged.original_raw_job or staged.raw_job or ""
        computed: str | None = _compute_split_suffix(
            original, staged.review_part_number_override
        )
        source = (
            "computed" if staged.review_split_suffix_override == computed else "operator"
        )
        assignments.append({"row_id": staged.id, "source": source})

    if assignments:
        bind.execute(
            sa.text(
                "UPDATE import_staging SET review_split_suffix_source = :source "
                "WHERE id = :row_id"
            ),
            assignments,
        )


def downgrade() -> None:
    """Patch 06 downgrade.

    Pre:  Schema is at migration 0013.
    Post: import_staging has no review_split_suffix_source column and no
          ck_import_staging_split_suffix_source constraint.
    Raises: nothing beyond normal SQLAlchemy/Alembic errors.
    """
    with op.batch_alter_table("import_staging", schema=None) as batch:
        batch.drop_constraint(_CHECK_NAME, type_="check")
        batch.drop_column("review_split_suffix_source")
