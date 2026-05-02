from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..models import ImportStagingRow, ImportStatus, Job
from ..transform import transform_staging_row
from .jobs import JOB_EXPAND_OPTIONS


def list_errored(
    session: Session, *, limit: int, offset: int,
) -> tuple[list[ImportStagingRow], int]:
    base = select(ImportStagingRow).where(
        ImportStagingRow.processing_status == ImportStatus.error
    )
    total = session.scalar(select(func.count()).select_from(base.subquery()))
    rows = session.scalars(
        base.order_by(ImportStagingRow.id)
        .limit(limit)
        .offset(offset)
    ).all()
    return list(rows), total


def get_staging_row(session: Session, row_id: int) -> ImportStagingRow | None:
    return session.get(ImportStagingRow, row_id)


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
