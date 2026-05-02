from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..models import ImportStagingRow, ImportStatus, Job
from ..transform import transform_staging_row
from .jobs import JOB_EXPAND_OPTIONS


class StagingDiscardError(Exception):
    """Row cannot be discarded because it has resolved to a Job."""


class StagingRestoreError(Exception):
    """Row cannot be restored because it is not currently discarded."""


def list_errored(
    session: Session, *, limit: int, offset: int,
) -> tuple[list[ImportStagingRow], int]:
    base = select(ImportStagingRow).where(
        ImportStagingRow.processing_status == ImportStatus.error,
        ImportStagingRow.discarded_at.is_(None),
    )
    total = session.scalar(select(func.count()).select_from(base.subquery()))
    rows = session.scalars(
        base.order_by(ImportStagingRow.id)
        .limit(limit)
        .offset(offset)
    ).all()
    return list(rows), total


def list_discarded(
    session: Session, *, limit: int, offset: int,
) -> tuple[list[ImportStagingRow], int]:
    base = select(ImportStagingRow).where(
        ImportStagingRow.discarded_at.is_not(None)
    )
    total = session.scalar(select(func.count()).select_from(base.subquery()))
    rows = session.scalars(
        base.order_by(
            ImportStagingRow.discarded_at.desc(),
            ImportStagingRow.id.desc(),
        )
        .limit(limit)
        .offset(offset)
    ).all()
    return list(rows), total


def get_staging_row(session: Session, row_id: int) -> ImportStagingRow | None:
    return session.get(ImportStagingRow, row_id)


def discard_staging_row(session: Session, row: ImportStagingRow) -> None:
    if row.resolved_job_id is not None:
        raise StagingDiscardError(
            f"Row {row.id} has resolved to job {row.resolved_job_id}; "
            "discard is not permitted on resolved rows."
        )
    if row.processing_status != ImportStatus.error:
        raise StagingDiscardError(
            f"Row {row.id} is in '{row.processing_status.value}' state; "
            "only errored rows can be discarded."
        )
    if row.discarded_at is not None:
        # Idempotent: already discarded; preserve the original timestamp.
        return
    row.discarded_at = datetime.now(UTC)
    session.commit()


def restore_staging_row(session: Session, row: ImportStagingRow) -> None:
    if row.discarded_at is None:
        raise StagingRestoreError(f"Row {row.id} is not discarded.")
    row.discarded_at = None
    session.commit()


def apply_correction(
    session: Session,
    row: ImportStagingRow,
    payload: dict[str, object],
) -> Job | None:
    """Apply a correction payload to `row`, re-run transform, commit, and return the resolved Job.

    Returns None when the transform produced an `errored` outcome — the caller should
    read `row.processing_error` for the user-facing detail.
    """
    for attr, value in payload.items():
        setattr(row, attr, value)

    row.processing_status = ImportStatus.pending
    row.processing_error = None
    row.suggested_correction = None
    row.resolved_job_id = None
    row.processed_at = None

    nested = session.begin_nested()
    try:
        outcome = transform_staging_row(session, row)
        nested.commit()
    except Exception:
        nested.rollback()
        raise

    session.commit()

    if outcome.action == "errored":
        return None

    return session.execute(
        select(Job)
        .where(Job.id == row.resolved_job_id)
        .options(*JOB_EXPAND_OPTIONS)
    ).unique().scalar_one()
