from __future__ import annotations

import logging
from datetime import UTC, datetime

from sqlalchemy import and_, func, select
from sqlalchemy.orm import Session
from sqlalchemy.sql.elements import ColumnElement

from ..extractors import decompose_job_string
from ..models import ImportStagingRow, ImportStatus, Job
from ..schemas import ConflictGroup, ConflictKind, StagingRowDetailRead, RestoreConflictPreview, IncomingRestoreCandidate, RestoreSourceKind, StagingRestoreAction
from ..transform import _mark_error, transform_staging_row
from .jobs import JOB_EXPAND_OPTIONS

log = logging.getLogger(__name__)


class StagingDiscardError(Exception):
    """Row cannot be discarded because it has resolved to a Job."""


class StagingRestoreError(Exception):
    """Row cannot be restored because it is not currently discarded."""


class StagingRestoreValidationError(Exception):
    """One of the actions in StagingRestoreRequest failed validation.

    Attributes:
        action_index: index into the actions list of the failing entry.
    """
    def __init__(self, detail: str, action_index: int) -> None:
        super().__init__(detail)
        self.detail = detail
        self.action_index = action_index


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
    qualifier_segment: str = decomp.build_qualifier.value if decomp.build_qualifier else ""
    return (
        f"{decomp.part_number}|{decomp.build_type.value}"
        f"|{decomp.split_suffix or ''}|{decomp.repeat_reference or ''}"
        f"|{qualifier_segment}"
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
# Savepoint error-capture helper (§3 of 20260503-ProjectRefactor02Patch01Implementation01.md)
# ---------------------------------------------------------------------------

def _rollback_with_error_capture(
    session: Session,
    row: ImportStagingRow,
    savepoint,
) -> None:
    """Roll back ``savepoint`` while preserving the error markers that
    :func:`~backend.app.transform._mark_error` wrote to ``row`` inside it.

    Pre-conditions:
      - ``row`` is attached to ``session``
      - ``row.processing_status == ImportStatus.error`` (i.e. ``_mark_error``
        already ran inside the savepoint)
      - ``savepoint`` is the open nested transaction wrapping the transform

    Post-conditions:
      - ``savepoint`` is closed via rollback
      - ``row.processing_status == ImportStatus.error``
      - ``row.processing_error`` matches the value ``_mark_error`` wrote
      - ``row.suggested_correction`` matches the value ``_mark_error`` wrote
      - No Job insert or mutation from inside the savepoint survives
    """
    captured_error: str | None = row.processing_error
    captured_suggestion: str | None = row.suggested_correction

    savepoint.rollback()

    row.processing_status = ImportStatus.error
    row.processing_error = captured_error
    row.suggested_correction = captured_suggestion


# ---------------------------------------------------------------------------
# Duplicate-collision message helpers (§6 of 20260503-ProjectRefactor02Patch01Implementation01.md)
# ---------------------------------------------------------------------------

def _duplicate_collision_error_message(new_key: str) -> str:
    """User-facing error for the sibling-collision arm of apply_correction."""
    return f"{_DUPLICATE_ERROR_PREFIX} {new_key} — a corrected row already resolves to this identity"


def _duplicate_collision_suggestion() -> str:
    """User-facing correction hint for a sibling-collision error."""
    return (
        "Another staging row already occupies this JOB identity. "
        "Add a split suffix (e.g. '-1par', '-2par') to make the identities unique."
    )


# ---------------------------------------------------------------------------
# Public service functions
# ---------------------------------------------------------------------------

def _identity_key_for_row(row: ImportStagingRow) -> str | None:
    """Compute the canonical identity key for a staging row from its current raw_job.

    Returns None when raw_job does not parse (row has no valid identity).
    """
    return _identity_key_after_payload(row, {})


def _identity_key_for_job(job) -> str | None:
    """Delegate to the authoritative implementation in services/jobs.py."""
    from .jobs import identity_key_for_job
    return identity_key_for_job(job)


# ---------------------------------------------------------------------------
# Restore-conflict preview (§4.2)
# ---------------------------------------------------------------------------

def preview_restore_staging(
    session: Session,
    row: ImportStagingRow,
) -> RestoreConflictPreview:
    """Compute what would collide if `row` (currently discarded) were restored.

    Pre:  row.discarded_at IS NOT NULL.
    Post: returns a RestoreConflictPreview with three collision sets:
            colliding_staging_errored_rows:   errored, non-discarded rows that
                                              share the same identity key.
            colliding_staging_discarded_rows: discarded rows (other than `row`)
                                              sharing the same identity key.
            colliding_live_jobs:              active (non-discarded) jobs sharing
                                              the same identity key.
          Never mutates state.
    """
    group_key = _identity_key_for_row(row) or ""

    errored_colliders: list[ImportStagingRow] = []
    discarded_colliders: list[ImportStagingRow] = []
    live_job_colliders: list[Job] = []

    if group_key:
        # Errored staging rows — non-discarded, error status, matching identity key.
        # We compare identity key at the Python level (not stored directly) because
        # the DB stores raw_job, not the decomposed key.
        # Exclude the target row itself (relevant when discarded_at was flushed to NULL
        # before calling this function, e.g. inside restore_staging_row_with_actions).
        candidate_errored = list(session.scalars(
            select(ImportStagingRow)
            .where(
                ImportStagingRow.processing_status == ImportStatus.error,
                ImportStagingRow.discarded_at.is_(None),
                ImportStagingRow.id != row.id,
            )
        ).all())
        for c in candidate_errored:
            if _identity_key_for_row(c) == group_key:
                errored_colliders.append(c)

        # Discarded staging rows (excluding self) — same identity key.
        candidate_discarded = list(session.scalars(
            select(ImportStagingRow)
            .where(
                ImportStagingRow.discarded_at.is_not(None),
                ImportStagingRow.id != row.id,
            )
        ).all())
        for c in candidate_discarded:
            if _identity_key_for_row(c) == group_key:
                discarded_colliders.append(c)

        # Live persisted jobs — active (superseded_at IS NULL, discarded_at IS NULL).
        from .jobs import JOB_EXPAND_OPTIONS
        from sqlalchemy.orm import Session as _Session
        candidate_jobs = list(session.scalars(
            select(Job)
            .where(Job.superseded_at.is_(None), Job.discarded_at.is_(None))
            .options(*JOB_EXPAND_OPTIONS)
        ).unique().all())
        for j in candidate_jobs:
            if _identity_key_for_job(j) == group_key:
                live_job_colliders.append(j)

    incoming = IncomingRestoreCandidate(
        kind=RestoreSourceKind.STAGING,
        staging=StagingRowDetailRead.model_validate(row),
        job=None,
    )

    from ..schemas import JobReadExpanded
    return RestoreConflictPreview(
        incoming=incoming,
        colliding_staging_errored_rows=[StagingRowDetailRead.model_validate(c) for c in errored_colliders],
        colliding_staging_discarded_rows=[StagingRowDetailRead.model_validate(c) for c in discarded_colliders],
        colliding_live_jobs=[JobReadExpanded.model_validate(j) for j in live_job_colliders],
        group_key=group_key,
    )


# ---------------------------------------------------------------------------
# Restore with actions (§4.2 existing route hardening)
# ---------------------------------------------------------------------------

def apply_restore_actions(
    session: Session,
    actions: list[StagingRestoreAction],
) -> None:
    """Apply a list of operator-resolution actions inside the caller's transaction.

    Each action references a staging row id. Actions are applied in array order.
    On the first validation failure, raises StagingRestoreValidationError with
    the failing action's index.

    Raises:
        StagingRestoreValidationError: on per-action failure (row not found,
            wrong type, invalid payload). The caller MUST roll back on this.
    """
    from ..schemas import StagingRowCorrectionRequest

    for idx, action in enumerate(actions):
        staging_row = session.get(ImportStagingRow, action.row_id)
        if staging_row is None:
            raise StagingRestoreValidationError(
                f"Action {idx}: staging row {action.row_id} not found.",
                idx,
            )

        if action.kind == "discard":
            if staging_row.resolved_job_id is not None:
                raise StagingRestoreValidationError(
                    f"Action {idx}: row {action.row_id} has resolved to a job; cannot discard.",
                    idx,
                )
            if staging_row.processing_status != ImportStatus.error:
                raise StagingRestoreValidationError(
                    f"Action {idx}: row {action.row_id} is not in error state; cannot discard.",
                    idx,
                )
            if staging_row.discarded_at is None:
                staging_row.discarded_at = datetime.now(UTC)

        elif action.kind == "edit":
            payload = action.payload or {}
            # Validate field names via the correction schema (extra='forbid').
            try:
                StagingRowCorrectionRequest(**payload)
            except Exception as exc:
                raise StagingRestoreValidationError(
                    f"Action {idx}: invalid edit payload — {exc}.",
                    idx,
                ) from exc
            for attr, value in payload.items():
                setattr(staging_row, attr, value)

        else:
            raise StagingRestoreValidationError(
                f"Action {idx}: unknown kind '{action.kind}'; expected 'edit' or 'discard'.",
                idx,
            )


def restore_staging_row_with_actions(
    session: Session,
    row: ImportStagingRow,
    actions: list[StagingRestoreAction],
) -> ImportStagingRow:
    """Apply `actions` and restore `row` inside a single transaction.

    Post: on success, row.discarded_at is None and the row is committed.
    Raises:
        StagingRestoreError: if row.discarded_at IS NULL (not discarded).
        StagingRestoreValidationError: if any action fails; transaction rolled back.
        StagingRestoreConflictError: if a residual identity collision remains after
            actions are applied; carries a fresh RestoreConflictPreview.
    """
    if row.discarded_at is None:
        raise StagingRestoreError(f"Row {row.id} is not discarded.")

    nested = session.begin_nested()
    try:
        apply_restore_actions(session, actions)
        row.discarded_at = None        # Flush so the DB reflects action changes before the preview queries run.
        # (Sessions are created with autoflush=False, so an explicit flush is required
        # here to avoid false collision detection against stale DB state.)
        session.flush()        # Check for residual collision after applying actions.
        preview = preview_restore_staging(session, row)
        # Only errored non-discarded rows and live jobs are genuine blockers.
        # Discarded colliders are informational (shown in preview UI only) and
        # do not prevent restore — the operator cannot act on them anyway.
        has_collision = (
            bool(preview.colliding_staging_errored_rows)
            or bool(preview.colliding_live_jobs)
        )
        if has_collision:
            nested.rollback()
            raise StagingRestoreConflictError(preview)
        nested.commit()
    except (StagingRestoreValidationError, StagingRestoreConflictError):
        # Conflict/validation errors rollback the savepoint then propagate.
        # For StagingRestoreConflictError we already called nested.rollback() above,
        # so a second rollback is a no-op on SQLite savepoints — safe to call.
        try:
            nested.rollback()
        except Exception:
            pass
        raise
    except Exception:
        nested.rollback()
        raise

    session.commit()
    return row


class StagingRestoreConflictError(Exception):
    """Restore cannot proceed because a residual collision was detected after actions.

    Carries the fresh RestoreConflictPreview for the response body.
    """
    def __init__(self, preview: RestoreConflictPreview) -> None:
        super().__init__("Residual collision after actions.")
        self.preview = preview


def list_errored(
    session: Session, *, limit: int, offset: int, search: str | None = None,
) -> tuple[list[ImportStagingRow], int]:
    # §3.6.1 Option B: exclude rows that belong to a duplicate group (they appear
    # in /conflicts only).
    base = select(ImportStagingRow).where(
        ImportStagingRow.processing_status == ImportStatus.error,
        ImportStagingRow.discarded_at.is_(None),
        ImportStagingRow.duplicate_group_key.is_(None),
    )
    if search:
        base = _apply_errored_search_clause(base, search.strip())
    total = session.scalar(select(func.count()).select_from(base.subquery()))
    rows = session.scalars(
        base.order_by(ImportStagingRow.id)
        .limit(limit)
        .offset(offset)
    ).all()
    return list(rows), total


def _apply_errored_search_clause(query, search: str):
    """Apply a single-pattern OR filter across the four searchable errored-row columns.

    Matching rules mirror the History endpoint's convention (single %pattern%,
    no tokenisation). Integer-only columns (source_row_number, batch_id) match
    only when the entire search string parses as an integer.
    """
    from sqlalchemy import or_

    pattern = f"%{search}%"
    int_val: int | None = None
    try:
        int_val = int(search)
    except ValueError:
        pass

    clauses = [
        ImportStagingRow.processing_error.ilike(pattern),
        ImportStagingRow.suggested_correction.ilike(pattern),
    ]
    if int_val is not None:
        clauses.append(ImportStagingRow.source_row_number == int_val)
        clauses.append(ImportStagingRow.batch_id == int_val)

    return query.where(or_(*clauses))


def list_discarded(
    session: Session, *, limit: int, offset: int, search: str | None = None,
) -> tuple[list[ImportStagingRow], int]:
    base = select(ImportStagingRow).where(
        ImportStagingRow.discarded_at.is_not(None)
    )
    if search:
        base = _apply_errored_search_clause(base, search.strip())
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

    final_disposition: str  # "processed" | "transform_errored" | "duplicate_collision"
    nested = session.begin_nested()
    try:
        outcome = transform_staging_row(session, row)
        if outcome.action == "errored":
            # Transform itself produced an error — roll back the Job mutation but
            # preserve the error markers _mark_error wrote to row.
            _rollback_with_error_capture(session, row, nested)
            final_disposition = "transform_errored"
        else:
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
                row.processing_error = _duplicate_collision_error_message(new_key)
                row.suggested_correction = _duplicate_collision_suggestion()
                row.resolved_job_id = None
                row.processed_at = None
                row.duplicate_group_key = new_key
                final_disposition = "duplicate_collision"
            else:
                nested.commit()
                row.duplicate_group_key = None
                final_disposition = "processed"
    except Exception:
        nested.rollback()
        raise

    if final_disposition == "transform_errored":
        # §3.3.2 audit 1.1: only assign group key when the error IS a duplicate
        # error; other error classes must not be relabeled.
        row.duplicate_group_key = (
            new_key if _is_intra_file_duplicate_error(row.processing_error) else None
        )

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

    if final_disposition != "processed":
        return None
    return _expanded_job(session, row.resolved_job_id)

