"""2nd OPS service layer (Phase 22 Part 2).

Trust boundary: the client parses the Audit BOM paste and maps the columns; the
server re-validates count, per-field widths and caps independently. The client's
mapping is never trusted.

There is no stored status. Three columns of state — Job.second_ops_reviewed_at,
the line count and Job.second_ops_unexpected_inclusions — derive all three UI
states, so there is no invariant keeping two tables in agreement.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
import logging
from typing import Literal

from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session

from ..config import Settings
from ..models import Job, JobSecondOpsLine, JobStatus
from ..schemas import (
    AuditBomFields,
    SecondOpsLimits,
    SecondOpsLine,
    SecondOpsRecord,
    SecondOpsState,
    SecondOpsSummary,
    SecondOpsWriteRequest,
)

logger = logging.getLogger(__name__)

JobId = int

_AUDIT_BOM_FIELD_NAMES: tuple[str, ...] = (
    "find_number",
    "component_part_number",
    "per_board_count",
    "ref_des",
    "description",
    "mount_type",
    "quantity_needed",
    "quantity_on_hand",
)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


# ---------------------------------------------------------------------------
# Failure values
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SecondOpsNotFound:
    """No Job row with this id exists. Discarded and superseded jobs are NOT
    this — they are returned, because History and the lineage surfaces render
    records for jobs the write guard has closed."""

    job_id: JobId


@dataclass(frozen=True)
class SecondOpsRejection:
    """A payload that failed a Settings-derived bound. Router maps it to 422."""

    field: Literal["lines", "unexpected_inclusions"]
    message: str


SecondOpsWriteFailureKind = Literal[
    "not_found", "discarded", "superseded", "shipped", "storage"
]


@dataclass(frozen=True)
class SecondOpsWriteFailure:
    """Why a write did not land.

    not_found                          -> 404
    discarded / superseded / shipped   -> 409, from the write guard
    storage                            -> 500, a caught IntegrityError /
                                          OperationalError
    """

    kind: SecondOpsWriteFailureKind
    message: str


@dataclass(frozen=True)
class ValidatedSecondOps:
    """A payload that has passed every Settings-derived bound.

    unexpected_inclusions is ALREADY stripped, or None when stripping emptied
    it. replace_second_ops writes it as-is and never strips again — two
    functions each trimming "harmlessly" is how a field ends up with two owners
    and no answer to what is stored.
    """

    lines: tuple[AuditBomFields, ...]
    unexpected_inclusions: str | None


# ---------------------------------------------------------------------------
# State derivation
# ---------------------------------------------------------------------------


def derive_second_ops_state(
    reviewed_at: datetime | None,
    line_count: int,
    has_unexpected_inclusions: bool,
) -> SecondOpsState:
    """Return the UI state implied by the three stored columns.

    Pre:   line_count is the true total, not a truncated preview length.
    Post:  unaudited      iff reviewed_at IS NULL
           not_applicable iff reviewed_at set, no lines and no note
           recorded       otherwise
           Pure; total. Shipping is not a transition and does not appear here —
           it revokes the write guard's permission, which removes every outbound
           edge while leaving the state itself unchanged.
    Raises: never.
    """
    if reviewed_at is None:
        return "unaudited"
    if line_count == 0 and not has_unexpected_inclusions:
        return "not_applicable"
    return "recorded"


def _note_is_present(note: str | None) -> bool:
    return bool(note and note.strip())


def _limits(settings: Settings) -> SecondOpsLimits:
    return SecondOpsLimits(
        max_lines=settings.second_ops_max_lines,
        note_max_chars=settings.second_ops_note_max_chars,
    )


# ---------------------------------------------------------------------------
# Read path
# ---------------------------------------------------------------------------


def load_second_ops_summaries(
    session: Session,
    page_jobs: Sequence[Job],
    preview_cap: int,
) -> Mapping[JobId, SecondOpsSummary]:
    """Build the bounded 2nd OPS summary for one page of jobs.

    Pre:   page_jobs are the Job instances the caller's page query already
           materialised, attached to session. preview_cap >= 0.
    Post:  returns exactly one entry per job in page_jobs, INCLUDING jobs with
           no lines — iteration is over page_jobs, not over query results, so a
           zero-line job cannot fall out of the mapping.
           state and has_unexpected_inclusions are read from
           job.second_ops_reviewed_at and job.second_ops_unexpected_inclusions,
           already loaded on the passed instances: no additional I/O. Taking ids
           instead would have forced a third query or a JOIN back to jobs to
           recover columns the caller had already loaded.
           line_count and preview come from two queries against
           job_second_ops_lines. Two queries total regardless of page size.
           The per-job cap is enforced in SQL by ROW_NUMBER() OVER (PARTITION BY
           job_id ORDER BY line_order), not by slicing in Python — 500 jobs at
           56 lines would be 28,000 rows on the page load otherwise. Window
           functions need SQLite >= 3.25; every CPython >= 3.10 ships well above
           that, and PyInstaller bundles the build interpreter's _sqlite3.
    Raises: never.
    """
    job_ids = [job.id for job in page_jobs]
    if not job_ids:
        return {}

    ranked = (
        select(
            JobSecondOpsLine.id,
            JobSecondOpsLine.job_id,
            JobSecondOpsLine.line_order,
            JobSecondOpsLine.find_number,
            JobSecondOpsLine.component_part_number,
            JobSecondOpsLine.per_board_count,
            JobSecondOpsLine.ref_des,
            JobSecondOpsLine.description,
            JobSecondOpsLine.mount_type,
            JobSecondOpsLine.quantity_needed,
            JobSecondOpsLine.quantity_on_hand,
            func.row_number()
            .over(
                partition_by=JobSecondOpsLine.job_id,
                order_by=JobSecondOpsLine.line_order,
            )
            .label("rn"),
        )
        .where(JobSecondOpsLine.job_id.in_(job_ids))
        .subquery()
    )

    previews: dict[JobId, list[SecondOpsLine]] = {}
    for row in session.execute(
        select(ranked)
        .where(ranked.c.rn <= preview_cap)
        .order_by(ranked.c.job_id, ranked.c.line_order)
    ).all():
        values = dict(row._mapping)
        values.pop("rn", None)
        previews.setdefault(values["job_id"], []).append(
            SecondOpsLine(
                id=values["id"],
                line_order=values["line_order"],
                **{name: values[name] for name in _AUDIT_BOM_FIELD_NAMES},
            )
        )

    counts: dict[JobId, int] = dict(
        session.execute(
            select(JobSecondOpsLine.job_id, func.count(JobSecondOpsLine.id))
            .where(JobSecondOpsLine.job_id.in_(job_ids))
            .group_by(JobSecondOpsLine.job_id)
        ).all()
    )

    summaries: dict[JobId, SecondOpsSummary] = {}
    for job in page_jobs:
        line_count = counts.get(job.id, 0)
        has_note = _note_is_present(job.second_ops_unexpected_inclusions)
        summaries[job.id] = SecondOpsSummary(
            state=derive_second_ops_state(
                job.second_ops_reviewed_at, line_count, has_note
            ),
            line_count=line_count,
            reviewed_at=job.second_ops_reviewed_at,
            has_unexpected_inclusions=has_note,
            preview=previews.get(job.id, []),
        )
    return summaries


def get_second_ops_record(
    session: Session,
    job_id: JobId,
    settings: Settings,
) -> SecondOpsRecord | SecondOpsNotFound:
    """Return the complete 2nd OPS record for one job.

    Pre:   none. job_id is unvalidated caller input.
    Post:  SecondOpsNotFound iff no Job row with that id exists. Discarded,
           superseded and shipped jobs ARE returned — reads are not guarded by
           status, only writes are, and History reads records the write guard
           has closed to editing.
           For a job with second_ops_reviewed_at IS NULL, returns a SYNTHESISED
           record — state "unaudited", empty lines, null reviewed_at, null note.
           "Never audited" is a state of the job, not the absence of a resource,
           so it must not be encoded as a missing one.
           lines are ordered by line_order ascending and are UNBOUNDED by the
           preview cap: this is the full-set read the bounded summary cannot
           serve.
           limits is built from the live Settings on every call, so a changed
           cap reaches the client without a rebuild.
    Raises: never.
    """
    job = session.get(Job, job_id)
    if job is None:
        return SecondOpsNotFound(job_id=job_id)

    lines = session.scalars(
        select(JobSecondOpsLine)
        .where(JobSecondOpsLine.job_id == job_id)
        .order_by(JobSecondOpsLine.line_order)
    ).all()

    note = job.second_ops_unexpected_inclusions
    return SecondOpsRecord(
        job_id=job.id,
        state=derive_second_ops_state(
            job.second_ops_reviewed_at, len(lines), _note_is_present(note)
        ),
        reviewed_at=job.second_ops_reviewed_at,
        unexpected_inclusions=note,
        lines=[SecondOpsLine.model_validate(line) for line in lines],
        limits=_limits(settings),
    )


# ---------------------------------------------------------------------------
# Write path
# ---------------------------------------------------------------------------


def validate_second_ops_payload(
    payload: SecondOpsWriteRequest,
    settings: Settings,
) -> ValidatedSecondOps | SecondOpsRejection:
    """Validate a client-supplied 2nd OPS payload before any write.

    Pre:   none — payload is untrusted client input. Per-field widths have
           already been enforced by AuditBomFields at the request boundary, so
           this function owns only the Settings-derived bounds.
    Post:  on success the line count is <= settings.second_ops_max_lines and the
           note is <= settings.second_ops_note_max_chars measured AFTER
           stripping.
           THIS FUNCTION OWNS THE ONLY STRIP IN THE WRITE PATH. The returned
           ValidatedSecondOps carries the note already stripped, or None when
           stripping empties it; replace_second_ops writes that value as-is.
           The eight line fields are carried through VERBATIM — no trim, no case
           change, no numeric coercion. The split is asymmetric on purpose: the
           note is prose and its outer whitespace is noise, the line fields are a
           transcription and their whitespace is data.
    Raises: never — the rejection is returned and the router maps it to 422.
    """
    if len(payload.lines) > settings.second_ops_max_lines:
        return SecondOpsRejection(
            field="lines",
            message=(
                f"{len(payload.lines)} lines exceeds the maximum of "
                f"{settings.second_ops_max_lines}."
            ),
        )

    raw_note = payload.unexpected_inclusions
    stripped_note = raw_note.strip() if raw_note is not None else None
    if stripped_note is not None and len(stripped_note) > settings.second_ops_note_max_chars:
        return SecondOpsRejection(
            field="unexpected_inclusions",
            message=(
                f"Note is {len(stripped_note)} characters; the maximum is "
                f"{settings.second_ops_note_max_chars}."
            ),
        )

    return ValidatedSecondOps(
        lines=tuple(payload.lines),
        unexpected_inclusions=stripped_note or None,
    )


def resolve_write_guard(job: Job | None, job_id: JobId) -> SecondOpsWriteFailure | None:
    """Decide whether job may be written, per-resource rather than per-caller.

    Pre:   job is the row fetched for job_id, or None.
    Post:  None when the write may proceed; otherwise the failure to return.
           The superseded_at arm is currently unreachable — nothing sets that
           column after migration 0011 removed the dead supersession work. It is
           present so the guard matches _active_jobs_base and stays correct if
           supersession returns.
    Raises: never.
    """
    if job is None:
        return SecondOpsWriteFailure(
            kind="not_found", message=f"Job {job_id} does not exist."
        )
    if job.discarded_at is not None:
        return SecondOpsWriteFailure(
            kind="discarded", message="This job has been discarded."
        )
    if job.superseded_at is not None:
        return SecondOpsWriteFailure(
            kind="superseded", message="This job has been superseded."
        )
    if job.status is JobStatus.shipped:
        return SecondOpsWriteFailure(
            kind="shipped", message="This job has shipped; its audit is frozen."
        )
    return None


def replace_second_ops(
    session: Session,
    job_id: JobId,
    payload: ValidatedSecondOps,
    settings: Settings,
    clock: Callable[[], datetime] = _utc_now,
) -> SecondOpsRecord | SecondOpsWriteFailure:
    """Replace the entire 2nd OPS record for a job. Whole-set replace, not a merge.

    Pre:   payload has already passed validate_second_ops_payload.
    Post:  on success — every prior JobSecondOpsLine for job_id is deleted; the
           submitted lines are inserted with line_order equal to submission
           order; second_ops_unexpected_inclusions is written EXACTLY as payload
           carries it, already stripped-or-null by validation;
           second_ops_reviewed_at := clock().
           This function performs no trimming, no case change and no
           normalization of any kind — validation owns all of it.
           An empty line set with a null note is the NOT_APPLICABLE state, not a
           return to unaudited — reviewed_at is stamped either way.
           On any failure nothing is written; delete and insert share one
           transaction.
           Concurrency is last-write-wins: there is no version token and no
           If-Match, so a second ACCEPT replaces the first with no warning. A
           deliberate acceptance; the escalation is one integer column compared
           on PUT.
    Raises: nothing under its own control. IntegrityError and OperationalError
           raised by the delete or the insert are caught, the savepoint is
           rolled back, and the failure is returned as
           SecondOpsWriteFailure(kind="storage"). Every other exception
           propagates — an unexpected error must not be laundered into a
           structured failure the caller will render as ordinary.
    """
    job = session.get(Job, job_id)
    guard = resolve_write_guard(job, job_id)
    if guard is not None:
        return guard
    assert job is not None  # resolve_write_guard returned not_found otherwise

    nested = session.begin_nested()
    try:
        session.execute(
            delete(JobSecondOpsLine).where(JobSecondOpsLine.job_id == job_id)
        )
        for line_order, line in enumerate(payload.lines):
            session.add(
                JobSecondOpsLine(
                    job_id=job_id,
                    line_order=line_order,
                    **{
                        name: getattr(line, name)
                        for name in _AUDIT_BOM_FIELD_NAMES
                    },
                )
            )
        job.second_ops_unexpected_inclusions = payload.unexpected_inclusions
        job.second_ops_reviewed_at = clock()
        session.flush()
        nested.commit()
    except (IntegrityError, OperationalError) as exc:
        nested.rollback()
        logger.warning(
            "second_ops.replace.storage_failure",
            extra={"job_id": job_id, "error": str(exc)},
        )
        return SecondOpsWriteFailure(
            kind="storage", message="The audit could not be saved."
        )

    record = get_second_ops_record(session, job_id, settings)
    assert isinstance(record, SecondOpsRecord)  # the job was fetched above
    return record
