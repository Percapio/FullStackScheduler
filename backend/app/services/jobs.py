from __future__ import annotations

import logging
from collections.abc import Iterator
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Literal

from sqlalchemy import Select, func, or_, select
from sqlalchemy.orm import Session, selectinload, undefer

from ..config import get_settings
from ..models import Assembly, BuildQualifier, BuildType, Customer, ImportStagingRow, ImportStatus, Job, JobStatus

if TYPE_CHECKING:
    from ..schemas import HistoryJobEditRequest

logger = logging.getLogger(__name__)

JOB_EXPAND_OPTIONS = (
    selectinload(Job.assembly).selectinload(Assembly.classifications),
    selectinload(Job.customer),
    selectinload(Job.salesperson),
)

EXPORT_LOAD_OPTIONS = (
    selectinload(Job.assembly),
    selectinload(Job.customer),
)


def _count(session: Session, base) -> int:
    return session.scalar(select(func.count()).select_from(base.subquery()))


def _attach_second_ops(session: Session, page_jobs: list[Job]) -> None:
    """Attach the bounded 2nd OPS summary to each Job on the page.

    Pre:   page_jobs are materialised Job instances attached to session.
    Post:  every instance carries a non-mapped `second_ops` attribute the schema
           reads. Applied by list_shipping and list_history ONLY — the other
           seven JobReadExpanded producers leave the attribute unset, and
           JobReadExpanded.second_ops defaults to None there.
           Deliberately NOT achieved by adding selectinload(Job.second_ops_lines)
           to JOB_EXPAND_OPTIONS: that would materialise every line of every job
           on every list page — MAX_PAGE_ROWS is 500 and audits run to ~50 lines.
    Raises: never.
    """
    from .second_ops import load_second_ops_summaries

    summaries = load_second_ops_summaries(
        session, page_jobs, get_settings().second_ops_preview_lines
    )
    for job in page_jobs:
        job.second_ops = summaries[job.id]


def _active_jobs_base() -> select:
    """Return a base SELECT over Jobs that are active: not superseded and not discarded.

    All active-job surfaces (list_jobs, list_shipping, get_job) must compose
    from this helper so both the superseded_at IS NULL and discarded_at IS NULL
    filters are applied uniformly.
    History / lineage queries use select(Job) directly and document the omission.
    """
    return select(Job).where(Job.superseded_at.is_(None), Job.discarded_at.is_(None))


def list_jobs(
    session: Session,
    *,
    status_filter: list[JobStatus] | None,
    assembly_id: int | None,
    customer_id: int | None,
    build_type: BuildType | None,
    limit: int,
    offset: int,
) -> tuple[list[Job], int]:
    base = _active_jobs_base()
    if status_filter is not None:
        base = base.where(Job.status.in_(status_filter))
    if assembly_id is not None:
        base = base.where(Job.assembly_id == assembly_id)
    if customer_id is not None:
        base = base.where(Job.customer_id == customer_id)
    if build_type is not None:
        base = base.where(Job.build_type == build_type)

    total = _count(session, base)
    rows = session.scalars(
        base.options(*JOB_EXPAND_OPTIONS)
        .order_by(Job.shipped_at.asc().nullslast(), Job.id.asc())
        .limit(limit)
        .offset(offset)
    ).unique().all()
    return list(rows), total


def list_shipping(
    session: Session, *, limit: int, offset: int,
) -> tuple[list[Job], int]:
    base = _active_jobs_base().where(Job.status != JobStatus.shipped)
    total = _count(session, base)
    rows = session.scalars(
        base.options(*JOB_EXPAND_OPTIONS)
        .order_by(Job.resolved_ship_date.asc().nullslast(), Job.id.asc())
        .limit(limit)
        .offset(offset)
    ).unique().all()
    page_jobs = list(rows)
    _attach_second_ops(session, page_jobs)
    return page_jobs, total


def build_history_query(
    search: str | None,
    include_superseded: bool = True,
) -> Select:
    """Build the SELECT that defines the History population.

    Pre:   search is a trimmed non-empty string or null.
    Post:  returns a Select over Job filtered to status == shipped,
           discarded_at IS NULL, superseded rows included, and — when search
           is present — the same five-field ILIKE disjunction over
           Assembly.part_number, Job.split_suffix, Job.repeat_reference,
           Customer.name and Assembly.base_mfg_notes.
           Carries no ordering, limit, offset or load options.
    Raises: never.
    """
    base = select(Job).where(Job.status == JobStatus.shipped, Job.discarded_at.is_(None))
    if not include_superseded:
        base = base.where(Job.superseded_at.is_(None))
    if search:
        pattern = f"%{search}%"
        base = (
            base.join(Job.assembly)
            .join(Job.customer)
            .where(
                or_(
                    Assembly.part_number.ilike(pattern),
                    Job.split_suffix.ilike(pattern),
                    Job.repeat_reference.ilike(pattern),
                    Customer.name.ilike(pattern),
                    Assembly.base_mfg_notes.ilike(pattern),
                )
            )
        )
    return base


def list_history(
    session: Session,
    *,
    search: str | None,
    limit: int,
    offset: int,
    include_superseded: bool = True,
) -> tuple[list[Job], int]:
    base = build_history_query(search=search, include_superseded=include_superseded)
    total = _count(session, base)
    rows = session.scalars(
        base.options(*JOB_EXPAND_OPTIONS)
        .order_by(Job.shipped_at.desc().nullslast(), Job.id.desc())
        .limit(limit)
        .offset(offset)
    ).unique().all()
    page_jobs = list(rows)
    _attach_second_ops(session, page_jobs)
    return page_jobs, total


def stream_history_for_export(
    session: Session,
    search: str | None,
    chunk_rows: int,
) -> Iterator[Job]:
    """Yield shipped Jobs matching the History filter, in grid order.

    Pre:   session is open and remains open for the life of the iterator.
    Post:  yields every matching Job exactly once, ordered
           shipped_at DESC NULLS LAST, id DESC. Resident rows never exceed
           chunk_rows. Does not mutate the session.
           second_ops_line_count is undeferred — one extra column on the
           existing select, no extra rows. Computing it inside the column
           renderer would be an N+1 across the entire export.
    Raises: propagates database errors to the caller.
    """
    base = build_history_query(search=search)
    query = (
        base.options(*EXPORT_LOAD_OPTIONS, undefer(Job.second_ops_line_count))
        .order_by(Job.shipped_at.desc().nullslast(), Job.id.desc())
    )
    # execution_options(yield_per=...), not Select.yield_per(): the latter is a
    # Query-era method and does not exist on Select under SQLAlchemy 2.x, which
    # made every export request raise AttributeError before this was fixed.
    for row in session.scalars(query.execution_options(yield_per=chunk_rows)):
        yield row


def get_job(session: Session, job_id: int) -> Job | None:
    return session.execute(
        _active_jobs_base()
        .where(Job.id == job_id)
        .options(*JOB_EXPAND_OPTIONS)
    ).unique().scalar_one_or_none()


def get_lineage(session: Session, job_id: int, *, include_superseded: bool = True) -> list[Job] | None:
    # Lineage is an audit surface; superseded rows are included by default (TDD §1 Epoch 1).
    anchor = session.execute(
        select(Job.repeat_reference, Assembly.part_number)
        .join(Assembly, Assembly.id == Job.assembly_id)
        .where(Job.id == job_id)
    ).one_or_none()
    if anchor is None:
        return None

    anchor_repeat_ref, anchor_part_number = anchor
    related_part_numbers: set[str] = {anchor_part_number}
    if anchor_repeat_ref:
        related_part_numbers.add(anchor_repeat_ref)

    chronology = func.coalesce(
        Job.shipped_at, Job.resolved_ship_date, func.date(Job.created_at)
    )
    base = (
        select(Job)
        .join(Assembly, Assembly.id == Job.assembly_id)
        .where(
            or_(
                Assembly.part_number.in_(related_part_numbers),
                Job.repeat_reference.in_(related_part_numbers),
            )
        )
    )
    if not include_superseded:
        base = base.where(Job.superseded_at.is_(None))
    rows = session.scalars(
        base.options(*JOB_EXPAND_OPTIONS)
        .order_by(chronology.asc(), Job.id.asc())
    ).unique().all()
    return list(rows)


class JobDiscardError(Exception):
    """Raised when a discard operation cannot proceed."""


def get_job_including_discarded(session: Session, job_id: int) -> Job | None:
    """Fetch a job by id regardless of discarded_at status.

    Used by the inspect-drawer pre-fetch and restore-preview to load rows
    that `get_job` (which filters discarded_at IS NULL) would miss.

    Pre:  job_id is a valid primary key.
    Post: returns the Job row or None if not found.
    """
    return session.execute(
        select(Job)
        .where(Job.id == job_id)
        .options(*JOB_EXPAND_OPTIONS)
    ).unique().scalar_one_or_none()


def discard_job(session: Session, job_id: int, reason: str) -> Job:
    """Soft-delete a Job by setting discarded_at to now().

    Pre:  job exists, discarded_at IS NULL. Shipped jobs are permitted.
    Post: discarded_at := now() UTC; row returned with the timestamp set.
          reason is written to logger.info and not persisted.

    Raises:
        JobDiscardError: if job not found (message starts with "not found"),
                         if already discarded (message starts with "already discarded").
    """
    job = get_job_including_discarded(session, job_id)
    if job is None:
        raise JobDiscardError(f"not found: job {job_id} does not exist")
    if job.discarded_at is not None:
        raise JobDiscardError(f"already discarded: job {job_id}")

    job.discarded_at = datetime.now(timezone.utc).replace(tzinfo=None)
    session.flush()
    logger.info("job %d discarded: %s", job_id, reason)
    return job


# ---------------------------------------------------------------------------
# Identity key helper (§7.2 — single source of truth for job-side identity)
# ---------------------------------------------------------------------------

def identity_key_for_job(job: Job) -> str | None:
    """Compute the canonical identity key for a persisted Job.

    Mirrors the staging row's _identity_key_after_payload but reads directly
    from Job fields. Returns None when build_type is None (should not occur
    for valid ingested rows).

    This is the authoritative implementation; services/staging.py imports it.
    """
    if job.build_type is None:
        return None
    qualifier_segment = job.build_qualifier.value if job.build_qualifier else ""
    assembly_part = job.assembly.part_number if job.assembly else ""
    return (
        f"{assembly_part}|{job.build_type.value}"
        f"|{job.split_suffix or ''}|{job.repeat_reference or ''}"
        f"|{qualifier_segment}"
    )


# ---------------------------------------------------------------------------
# Discarded jobs list (§6.2)
# ---------------------------------------------------------------------------

def list_discarded_jobs(
    session: Session,
    *,
    limit: int,
    offset: int,
    search: str | None = None,
) -> tuple[list[Job], int]:
    """List discarded jobs (discarded_at IS NOT NULL), with optional search.

    Search rules (single-pattern column-OR, mirroring History):
        - assembly.part_number   ILIKE %search%
        - customer.name          ILIKE %search%
        - job.split_suffix       ILIKE %search%
        - job.repeat_reference   ILIKE %search%
        - job.id                 = int(search) iff parses as int
    """
    base = select(Job).where(Job.discarded_at.is_not(None))
    if search:
        pattern = f"%{search}%"
        int_val: int | None = None
        try:
            int_val = int(search)
        except ValueError:
            pass
        base = base.join(Job.assembly).join(Job.customer)
        clauses = [
            Assembly.part_number.ilike(pattern),
            Customer.name.ilike(pattern),
            Job.split_suffix.ilike(pattern),
            Job.repeat_reference.ilike(pattern),
        ]
        if int_val is not None:
            clauses.append(Job.id == int_val)
        base = base.where(or_(*clauses))
    total = _count(session, base)
    rows = session.scalars(
        base.options(*JOB_EXPAND_OPTIONS)
        .order_by(Job.discarded_at.desc().nullslast(), Job.id.desc())
        .limit(limit)
        .offset(offset)
    ).unique().all()
    return list(rows), total


# ---------------------------------------------------------------------------
# Restore-conflict preview (§6.2)
# ---------------------------------------------------------------------------

class JobRestoreError(Exception):
    """Raised when a restore operation cannot proceed."""


class JobRestoreConflictError(Exception):
    """Residual collision after applying actions; carries a fresh preview."""

    def __init__(self, preview) -> None:
        super().__init__("Residual collision after actions.")
        self.preview = preview


def _build_restore_preview_for_job(session: Session, job: "Job") -> "RestoreConflictPreview":
    """Build a RestoreConflictPreview for *job* without checking discarded_at.

    This is the inner collision-detection step.  preview_restore_job adds the
    guard checks on top; restore_job_with_actions calls this directly after
    clearing discarded_at so the guard would spuriously fail.
    """
    from ..schemas import (
        IncomingRestoreCandidate,
        JobReadExpanded as JobReadExpandedSchema,
        RestoreConflictPreview,
        RestoreSourceKind,
        StagingRowDetailRead,
    )
    from .staging import _identity_key_for_row

    job_id = job.id
    group_key = identity_key_for_job(job) or ""

    errored_colliders: list[ImportStagingRow] = []
    discarded_colliders: list[ImportStagingRow] = []
    live_job_colliders: list[Job] = []

    if group_key:
        candidate_errored = list(session.scalars(
            select(ImportStagingRow).where(
                ImportStagingRow.processing_status == ImportStatus.error,
                ImportStagingRow.discarded_at.is_(None),
            )
        ).all())
        for c in candidate_errored:
            if _identity_key_for_row(c) == group_key:
                errored_colliders.append(c)

        candidate_discarded = list(session.scalars(
            select(ImportStagingRow).where(
                ImportStagingRow.discarded_at.is_not(None)
            )
        ).all())
        for c in candidate_discarded:
            if _identity_key_for_row(c) == group_key:
                discarded_colliders.append(c)

        candidate_jobs = list(session.scalars(
            select(Job)
            .where(
                Job.superseded_at.is_(None),
                Job.discarded_at.is_(None),
                Job.id != job_id,
            )
            .options(*JOB_EXPAND_OPTIONS)
        ).unique().all())
        for j in candidate_jobs:
            if identity_key_for_job(j) == group_key:
                live_job_colliders.append(j)

    incoming = IncomingRestoreCandidate(
        kind=RestoreSourceKind.JOB,
        staging=None,
        job=JobReadExpandedSchema.model_validate(job),
    )

    return RestoreConflictPreview(
        incoming=incoming,
        colliding_staging_errored_rows=[StagingRowDetailRead.model_validate(c) for c in errored_colliders],
        colliding_staging_discarded_rows=[StagingRowDetailRead.model_validate(c) for c in discarded_colliders],
        colliding_live_jobs=[JobReadExpandedSchema.model_validate(j) for j in live_job_colliders],
        group_key=group_key,
    )


def preview_restore_job(session: Session, job_id: int):
    """Compute what would collide if the discarded job were restored.

    Pre:  job exists and discarded_at IS NOT NULL.
    Post: returns a RestoreConflictPreview; never mutates state.

    Raises:
        JobRestoreError: if job not found (starts "not found") or
                         if job is not discarded (starts "not discarded").
    """
    job = get_job_including_discarded(session, job_id)
    if job is None:
        raise JobRestoreError(f"not found: job {job_id} does not exist")
    if job.discarded_at is None:
        raise JobRestoreError(f"not discarded: job {job_id} is not currently discarded")

    return _build_restore_preview_for_job(session, job)


# ---------------------------------------------------------------------------
# Restore with actions (§6.2)
# ---------------------------------------------------------------------------

def restore_job_with_actions(session: Session, job_id: int, actions: list) -> Job:
    """Apply staging `actions` and restore the discarded job inside a single transaction.

    Pre:  job exists and discarded_at IS NOT NULL.
    Post: on success, job.discarded_at is None and the session is committed.

    Raises:
        JobRestoreError: if job not found or not discarded.
        StagingRestoreValidationError (from staging): if any action fails;
            transaction rolled back.
        JobRestoreConflictError: if a residual identity collision remains after
            actions are applied; carries a fresh RestoreConflictPreview.
    """
    from .staging import StagingRestoreValidationError, apply_restore_actions

    job = get_job_including_discarded(session, job_id)
    if job is None:
        raise JobRestoreError(f"not found: job {job_id} does not exist")
    if job.discarded_at is None:
        raise JobRestoreError(f"not discarded: job {job_id} is not currently discarded")

    nested = session.begin_nested()
    try:
        apply_restore_actions(session, actions)
        job.discarded_at = None
        # Flush so the DB reflects the restored state before the preview query runs.
        session.flush()
        # Use internal helper — preview_restore_job would fail because discarded_at is now None.
        preview = _build_restore_preview_for_job(session, job)
        has_collision = (
            bool(preview.colliding_staging_errored_rows)
            or bool(preview.colliding_live_jobs)
        )
        if has_collision:
            nested.rollback()
            raise JobRestoreConflictError(preview)
        nested.commit()
    except (StagingRestoreValidationError, JobRestoreConflictError):
        try:
            nested.rollback()
        except Exception:
            pass
        raise
    except Exception:
        nested.rollback()
        raise

    session.commit()
    return job


# ---------------------------------------------------------------------------
# History job edit (Phase 17)
# ---------------------------------------------------------------------------

class JobEditError(Exception):
    """Raised when a History edit cannot proceed."""


class JobEditNotFoundError(JobEditError):
    """job_id does not exist."""

    def __init__(self, job_id: int) -> None:
        super().__init__(f"job {job_id} not found")
        self.job_id = job_id


class JobEditNotEditableError(JobEditError):
    """Job exists but cannot be edited in the History context.

    ``kind`` is one of:
        "not_shipped"  — status != shipped
        "discarded"    — discarded_at IS NOT NULL
    """

    def __init__(self, job_id: int, kind: Literal["not_shipped", "discarded"]) -> None:
        super().__init__(f"job {job_id} not editable: {kind}")
        self.job_id = job_id
        self.kind = kind


class JobEditValidationError(JobEditError):
    """Carries a per-field message for 422 surfaces."""

    def __init__(
        self,
        field: Literal["raw_job", "raw_customer", "raw_qty", "raw_shipped"],
        message: str,
    ) -> None:
        super().__init__(f"{field}: {message}")
        self.field = field
        self.message = message


class JobEditIdentityCollisionError(JobEditError):
    """Raised when re-decomposing raw_job would collide with another active job."""

    def __init__(self, colliding_job_id: int) -> None:
        super().__init__(f"identity collision with job {colliding_job_id}")
        self.colliding_job_id = colliding_job_id


def _was_sent(edit: "HistoryJobEditRequest", field_name: str) -> bool:
    """True iff the field was explicitly included in the request body."""
    return field_name in edit.model_fields_set


def _normalise_identity(edit: "HistoryJobEditRequest", field_name: str) -> str | None:
    """Return None (absent), '' (client sent intent-to-clear), or trimmed value."""
    if not _was_sent(edit, field_name):
        return None
    raw_value = getattr(edit, field_name)
    if raw_value is None:
        return ""
    return raw_value.strip()


def parse_positive_int(raw: str) -> int | None:
    """Parse a positive integer from a raw string; returns None on failure or if <= 0."""
    token = raw.strip()
    if not token:
        return None
    try:
        v = int(token)
    except ValueError:
        return None
    return v if v > 0 else None


def find_active_job_with_identity(
    session: Session, *, key: str, exclude_job_id: int,
) -> int | None:
    """Return the id of an active job (not superseded, not discarded) whose identity key
    matches ``key``, excluding ``exclude_job_id``.  Returns None if none found.
    """
    candidates = session.scalars(
        select(Job)
        .where(
            Job.superseded_at.is_(None),
            Job.discarded_at.is_(None),
            Job.id != exclude_job_id,
        )
        .options(*JOB_EXPAND_OPTIONS)
    ).unique().all()
    for candidate in candidates:
        if identity_key_for_job(candidate) == key:
            return candidate.id
    return None


def edit_history_job(
    session: Session,
    job_id: int,
    edit: "HistoryJobEditRequest",
) -> Job:
    """Edit reconciliation-style fields of a shipped job.

    Pre:  job exists, discarded_at IS NULL, status == shipped.
          At least one identity or ship-time field is set in the request body
          (enforced by HistoryJobEditRequest model_validator at the API layer).
    Post: derived Job fields updated atomically; staging row untouched;
          caller commits. Returns the refreshed Job with eager-loaded relations.

    Raises:
        JobEditNotFoundError              -> 404
        JobEditNotEditableError           -> 409
        JobEditValidationError            -> 422
        JobEditIdentityCollisionError     -> 409
    """
    from ..extractors import extract_shipped_date
    from ..services.upserts import upsert_assembly_by_part_number, upsert_customer

    job = session.execute(
        select(Job).where(Job.id == job_id).options(*JOB_EXPAND_OPTIONS)
    ).unique().scalar_one_or_none()

    if job is None:
        raise JobEditNotFoundError(job_id)
    if job.discarded_at is not None:
        raise JobEditNotEditableError(job_id, "discarded")
    if job.status != JobStatus.shipped:
        raise JobEditNotEditableError(job_id, "not_shipped")

    # ── Identity fields (PATCH semantics — absent vs null/empty distinguished) ──

    pn = _normalise_identity(edit, "part_number")
    if pn is not None:
        if pn == "":
            raise JobEditValidationError(
                field="part_number", message="part_number cannot be empty"
            )
        job.assembly_id = upsert_assembly_by_part_number(session, pn).id

    bt = _normalise_identity(edit, "build_type")
    if bt is not None:
        if bt == "":
            raise JobEditValidationError(
                field="build_type", message="build_type cannot be cleared"
            )
        bt_lower = bt.lower()
        valid_build_types = {e.value for e in BuildType}
        if bt_lower not in valid_build_types:
            raise JobEditValidationError(
                field="build_type",
                message=f"build_type must be one of {sorted(valid_build_types)}",
            )
        job.build_type = BuildType(bt_lower)

    ss = _normalise_identity(edit, "split_suffix")
    if ss is not None:
        if len(ss) > 32:
            raise JobEditValidationError(
                field="split_suffix", message="split_suffix exceeds 32 characters"
            )
        job.split_suffix = ss or None  # "" -> NULL

    rr = _normalise_identity(edit, "repeat_reference")
    if rr is not None:
        if len(rr) > 32:
            raise JobEditValidationError(
                field="repeat_reference", message="repeat_reference exceeds 32 characters"
            )
        job.repeat_reference = rr or None

    bq = _normalise_identity(edit, "build_qualifier")
    if bq is not None:
        if bq == "":
            job.build_qualifier = None
        else:
            bq_lower = bq.lower()
            valid_qualifiers = {e.value for e in BuildQualifier}
            if bq_lower not in valid_qualifiers:
                raise JobEditValidationError(
                    field="build_qualifier",
                    message=f"build_qualifier must be one of {sorted(valid_qualifiers)}",
                )
            job.build_qualifier = BuildQualifier(bq_lower)

    # ── Ship-time fields (Phase 17 semantics — is-not-None check) ──

    if edit.raw_qty is not None:
        parsed_quantity = parse_positive_int(edit.raw_qty)
        if parsed_quantity is None:
            raise JobEditValidationError(
                field="raw_qty", message=f"Invalid QTY: {edit.raw_qty!r}"
            )
        job.quantity = parsed_quantity

    if edit.raw_customer is not None:
        customer_name = edit.raw_customer.strip()
        if not customer_name:
            raise JobEditValidationError(
                field="raw_customer", message="raw_customer is empty"
            )
        job.customer_id = upsert_customer(session, customer_name).id

    if edit.raw_shipped is not None:
        parsed_shipped = extract_shipped_date(edit.raw_shipped)
        if parsed_shipped is None:
            raise JobEditValidationError(
                field="raw_shipped",
                message=f"Unparseable SHIPPED date: {edit.raw_shipped!r}",
            )
        job.shipped_at = parsed_shipped
        # job.ever_shipped_at intentionally NOT touched — INV-S3 (Phase 16 §3.4).

    # ── Identity-collision check after all identity-touching mutations ──
    # Flush first so that assembly_id FK is visible to identity_key_for_job.
    session.flush()
    session.refresh(job, ["assembly", "customer"])

    new_key = identity_key_for_job(job)
    if new_key is None:
        # build_type is NULL on this row (corrupted historical state); skip check.
        logger.warning(
            "job %d edited without identity-collision check: "
            "build_type is NULL (corrupted historical state)",
            job.id,
        )
    else:
        collider_id = find_active_job_with_identity(
            session, key=new_key, exclude_job_id=job.id
        )
        if collider_id is not None:
            raise JobEditIdentityCollisionError(colliding_job_id=collider_id)

    logger.info("job %d edited: %s", job.id, edit.reason)
    return job

