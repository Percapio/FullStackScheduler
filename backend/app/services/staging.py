from __future__ import annotations

import logging
from datetime import UTC, datetime

from sqlalchemy import and_, func, select
from sqlalchemy.orm import Session
from sqlalchemy.sql.elements import ColumnElement

from ..extractors import decompose_job_string
from ..models import ImportStagingRow, ImportStatus, Job
from ..schemas import ConflictGroup, ConflictKind, StagingRowDetailRead
from ..transform import _mark_error, transform_staging_row
from .jobs import JOB_EXPAND_OPTIONS

log = logging.getLogger(__name__)


class StagingDiscardError(Exception):
    """Row cannot be discarded because it has resolved to a Job."""


class StagingRestoreError(Exception):
    """Row cannot be restored because it is not currently discarded."""


# ---------------------------------------------------------------------------
# Single-source-of-truth predicate (§3.2.1)
# ---------------------------------------------------------------------------

def _active_duplicate_predicate(
    *,
    batch_id: int | None = None,
    group_key: str | None = None,
    exclude_id: int | None = None,
) -> ColumnElement[bool]:
    """Boolean predicate selecting ImportStagingRows currently active in an
    unresolved intra-file duplicate group: error status, non-discarded, non-null
    duplicate_group_key.  Optional batch_id / group_key / exclude_id narrow the scope.

    Single source of truth for _reevaluate_group_siblings (§3.2.2),
    list_conflicts (§3.4), and _count_active_siblings (§3.3.1).
    """
    clauses: list = [
        ImportStagingRow.processing_status == ImportStatus.error,
        ImportStagingRow.discarded_at.is_(None),
        ImportStagingRow.duplicate_group_key.is_not(None),
    ]
    if batch_id is not None:
        clauses.append(ImportStagingRow.batch_id == batch_id)
    if group_key is not None:
        clauses.append(ImportStagingRow.duplicate_group_key == group_key)
    if exclude_id is not None:
        clauses.append(ImportStagingRow.id != exclude_id)
    return and_(*clauses)


# ---------------------------------------------------------------------------
# Supporting helpers (§3.3.1)
# ---------------------------------------------------------------------------

_DUPLICATE_ERROR_PREFIX: str = "Intra-file duplicate JOB identity"


def _is_intra_file_duplicate_error(processing_error: str | None) -> bool:
    """True iff processing_error is an intra-file-duplicate-flavoured message.

    The substring constant must match the prefix written by ingest.py Stage 4
    and by _reevaluate_group_siblings — pinned by test_helper_message_format_matches_ingest.
    """
    return bool(processing_error) and processing_error.startswith(_DUPLICATE_ERROR_PREFIX)


def _identity_key_after_payload(
    row: ImportStagingRow,
    payload: dict[str, object],
) -> str | None:
    """Returns the canonical identity key the row WILL have once payload is applied,
    without mutating the row.  Returns None if raw_job (post-payload) does not parse.
    """
    new_raw_job: str | None = payload.get("raw_job", row.raw_job)  # type: ignore[assignment]
    if new_raw_job is None:
        return None
    decomp = decompose_job_string(new_raw_job)
    if decomp is None:
        return None
    return (
        f"{decomp.part_number}|{decomp.build_type.value}"
        f"|{decomp.split_suffix or ''}|{decomp.repeat_reference or ''}"
    )


def _count_active_siblings(
    session: Session,
    batch_id: int,
    group_key: str,
    exclude_id: int | None = None,
) -> int:
    """Count ImportStagingRows that are active in the given (batch_id, group_key) group."""
    return session.scalar(
        select(func.count()).select_from(
            select(ImportStagingRow)
            .where(_active_duplicate_predicate(batch_id=batch_id, group_key=group_key, exclude_id=exclude_id))
            .subquery()
        )
    ) or 0


# ---------------------------------------------------------------------------
# Sibling re-evaluation helper (§3.2.2)
# ---------------------------------------------------------------------------

def _reevaluate_group_siblings(
    session: Session,
    batch_id: int,
    group_key: str,
    exclude_id: int | None,
) -> None:
    """Re-evaluate the duplicate population at (batch_id, group_key) after one row
    has changed state.

    Post-conditions:
      - population >= 2: each survivor rewritten with current id list via _mark_error.
      - population == 1: lone survivor cleared and re-run through transform_staging_row
        inside a SAVEPOINT.  On SAVEPOINT failure, the row is re-marked errored with a
        recovery message.
      - population == 0: no-op.
    """
    survivors: list[ImportStagingRow] = list(session.scalars(
        select(ImportStagingRow)
        .where(_active_duplicate_predicate(batch_id=batch_id, group_key=group_key, exclude_id=exclude_id))
        .order_by(ImportStagingRow.id)
    ).all())

    if not survivors:
        return

    if len(survivors) >= 2:
        ids: list[int] = [s.id for s in survivors]
        part_number: str = group_key.split("|", 1)[0]
        message: str = f"{_DUPLICATE_ERROR_PREFIX} {group_key} (staging rows {ids})"
        suggestion: str = (
            "This JOB identity appears more than once in the workbook. "
            f"Add a split suffix to distinguish the builds \u2014 e.g., change the JOB to "
            f"'{part_number}-1par', '{part_number}-2par', etc."
        )
        for s in survivors:
            _mark_error(s, message, suggestion=suggestion)
        return

    lone: ImportStagingRow = survivors[0]
    # Clear the group key unconditionally BEFORE the SAVEPOINT so a rollback
    # on transform failure does not restore it.  A lone survivor is never part
    # of an active group regardless of whether the re-transform succeeds.
    lone.duplicate_group_key = None
    nested = session.begin_nested()
    try:
        lone.processing_status = ImportStatus.pending
        lone.processing_error = None
        lone.suggested_correction = None
        transform_staging_row(session, lone)
        nested.commit()
    except Exception as exc:
        nested.rollback()
        _mark_error(
            lone,
            f"Re-evaluation failure after sibling change: {type(exc).__name__}: {exc}",
            suggestion="Re-submit the row's corrections to retry.",
        )


# ---------------------------------------------------------------------------
# Query helpers
# ---------------------------------------------------------------------------

def _expanded_job(session: Session, job_id: int) -> Job:
    return session.execute(
        select(Job)
        .where(Job.id == job_id)
        .options(*JOB_EXPAND_OPTIONS)
    ).unique().scalar_one()


# ---------------------------------------------------------------------------
# Public service functions
# ---------------------------------------------------------------------------

def list_errored(
    session: Session, *, limit: int, offset: int,
) -> tuple[list[ImportStagingRow], int]:
    # §3.6.1 Option B: exclude rows that belong to a duplicate group (they appear
    # in /conflicts only).
    base = select(ImportStagingRow).where(
        ImportStagingRow.processing_status == ImportStatus.error,
        ImportStagingRow.discarded_at.is_(None),
        ImportStagingRow.duplicate_group_key.is_(None),
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


def list_conflicts(session: Session) -> list[ConflictGroup]:
    """Return all active intra-file duplicate groups, each with >= 2 members."""
    rows: list[ImportStagingRow] = list(session.scalars(
        select(ImportStagingRow)
        .where(_active_duplicate_predicate())
        .order_by(
            ImportStagingRow.batch_id,
            ImportStagingRow.duplicate_group_key,
            ImportStagingRow.id,
        )
    ).all())

    grouped: dict[tuple[int, str], list[ImportStagingRow]] = {}
    for r in rows:
        grouped.setdefault((r.batch_id, r.duplicate_group_key), []).append(r)

    out: list[ConflictGroup] = []
    for (batch_id, key), members in grouped.items():
        if len(members) < 2:
            # Invariant violation: the helper should have cleared the lone survivor's key.
            log.error(
                "conflict_singleton_invariant_violated",
                extra={"batch_id": batch_id, "group_key": key, "ids": [m.id for m in members]},
            )
            continue
        out.append(ConflictGroup(
            batch_id=batch_id,
            group_key=key,
            kind=ConflictKind.intra_file_duplicate,
            rows=[StagingRowDetailRead.model_validate(r) for r in members],
        ))
    return out


def get_staging_row(session: Session, row_id: int) -> ImportStagingRow | None:
    return session.get(ImportStagingRow, row_id)


def discard_staging_row(session: Session, row: ImportStagingRow) -> None:
    """Discard a staging row.

    Clears duplicate_group_key on the NULL→set transition and fires sibling
    re-evaluation if the row was part of a group.  Idempotent on already-discarded rows.
    """
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

    batch_id, key = row.batch_id, row.duplicate_group_key
    row.discarded_at = datetime.now(UTC)
    row.duplicate_group_key = None
    session.commit()

    if key is not None:
        _reevaluate_group_siblings(session, batch_id, key, exclude_id=row.id)
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
    """Apply a correction payload to `row`, re-run transform, commit, and return
    the resolved Job.

    Returns None when the transform produced an `errored` outcome.  Handles
    group-key drift, collision prevention, and sibling re-evaluation per §3.3.2.
    """
    old_batch_id: int = row.batch_id
    old_key: str | None = row.duplicate_group_key
    new_key: str | None = _identity_key_after_payload(row, payload)

    for attr, value in payload.items():
        setattr(row, attr, value)

    row.processing_status = ImportStatus.pending
    row.processing_error = None
    row.suggested_correction = None
    row.resolved_job_id = None
    row.processed_at = None

    outcome_action: str
    nested = session.begin_nested()
    try:
        outcome = transform_staging_row(session, row)
        if outcome.action != "errored":
            # Transform succeeded — but check whether the new identity has active
            # staging siblings.  If so, roll back the job insert/update and join
            # the duplicate group instead (§3.3.2 data-corruption hazard).
            siblings_at_new_key: int = (
                _count_active_siblings(session, row.batch_id, new_key, exclude_id=row.id)
                if new_key is not None else 0
            )
            if siblings_at_new_key >= 1:
                nested.rollback()
                row.processing_status = ImportStatus.error
                row.resolved_job_id = None
                row.processed_at = None
                row.duplicate_group_key = new_key
                outcome_action = "errored"
            else:
                nested.commit()
                row.duplicate_group_key = None
                outcome_action = outcome.action
        else:
            # Transform itself produced an error (not a duplicate-group issue).
            nested.commit()
            # §3.3.2 audit 1.1: only assign group key when the error IS a duplicate
            # error; other error classes must not be relabeled.
            row.duplicate_group_key = (
                new_key if _is_intra_file_duplicate_error(row.processing_error) else None
            )
            outcome_action = "errored"
    except Exception:
        nested.rollback()
        raise

    # duplicate_group_key must be set before commit so the helper's WHERE finds this row.
    session.commit()

    # Re-evaluate the old group if this row left it.
    if old_key is not None and old_key != row.duplicate_group_key:
        _reevaluate_group_siblings(session, old_batch_id, old_key, exclude_id=row.id)
        session.commit()

    # Re-evaluate the current group whenever this row is a member (exclude_id=None
    # so the row itself appears in the survivor set — see §3.3.2 design property 1).
    if row.duplicate_group_key is not None:
        _reevaluate_group_siblings(session, row.batch_id, row.duplicate_group_key, exclude_id=None)
        session.commit()

    if outcome_action == "errored":
        return None
    return _expanded_job(session, row.resolved_job_id)

