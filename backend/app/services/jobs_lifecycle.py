"""Lifecycle operations for Job supersession.

Epoch 2: shipped-history shield.
Epoch 3: candidate detection (CandidateDelta, detect_supersession_candidates,
          _infer_reason).
Resolution services are added in Epoch 4.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import (
    CandidateReason,
    CandidateResolution,
    ImportBatch,
    ImportStagingRow,
    ImportStatus,
    Job,
    JobSupersessionCandidate,
    SheetKind,
)

log = logging.getLogger(__name__)


def _now_utc() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


# ---------------------------------------------------------------------------
# Shield (Epoch 2)
# ---------------------------------------------------------------------------


def _has_any_shipped_history(session: Session, job: Job) -> bool:  # noqa: ARG001
    """Return True iff *job* has ever been shipped.

    Pre:  job is attached to session.
    Post: Returns True iff job.shipped_at IS NOT NULL.
          Returns False otherwise.
    Raises: never.

    Invariant (upheld by transform._apply_shipped): shipped_at is set
    atomically with status = JobStatus.shipped and is never cleared.
    Therefore shipped_at IS NOT NULL is a complete proxy for
    "has shipped at least once."
    """
    return job.shipped_at is not None


# ---------------------------------------------------------------------------
# Candidate detection (Epoch 3)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CandidateDelta:
    """Immutable summary of one detection pass.

    Pre:  all tuple elements are valid DB ids or assembly ids.
    Post: all tuples are de-duplicated by the service layer; order is
          insertion order within each category.
    """

    new_pending_candidate_ids: tuple[int, ...]
    auto_returned_candidate_ids: tuple[int, ...]
    skipped_shipped_job_ids: tuple[int, ...]
    skipped_already_pending_job_ids: tuple[int, ...]
    touched_assembly_ids: tuple[int, ...]

    @classmethod
    def empty(cls) -> CandidateDelta:
        """Return the zero-delta for historical batches or empty live batches."""
        return cls(
            new_pending_candidate_ids=(),
            auto_returned_candidate_ids=(),
            skipped_shipped_job_ids=(),
            skipped_already_pending_job_ids=(),
            touched_assembly_ids=(),
        )


def _infer_reason(
    session: Session,
    job: Job,
    referenced_job_ids: set[int],
) -> CandidateReason:
    """Classify the orphan for operator UX badge text.

    Pre:  job.id is NOT in referenced_job_ids.  session is live.
    Post: Comparisons use IS NOT DISTINCT FROM for nullable columns
          (matching transform.py convention — audit #10).
          Returns CandidateReason.orphan_after_split when ≥2 referenced
          jobs share (assembly_id, build_type, repeat_reference,
          build_qualifier) with the orphan AND have non-empty split_suffix.
          Returns CandidateReason.orphan_after_recombine when exactly one
          referenced job satisfies the match AND has split_suffix IS NULL
          while the orphan's split_suffix IS NOT NULL.
          Returns CandidateReason.orphan_other in every other shape.
    Raises: never.
    """
    if not referenced_job_ids:
        return CandidateReason.orphan_other

    siblings = session.scalars(
        select(Job)
        .where(Job.id.in_(list(referenced_job_ids)))
        .where(Job.assembly_id == job.assembly_id)
        .where(Job.build_type.is_not_distinct_from(job.build_type))
        .where(Job.repeat_reference.is_not_distinct_from(job.repeat_reference))
        .where(Job.build_qualifier.is_not_distinct_from(job.build_qualifier))
    ).all()

    split_siblings = [j for j in siblings if j.split_suffix]

    if len(split_siblings) >= 2:
        return CandidateReason.orphan_after_split

    if (
        len(siblings) == 1
        and siblings[0].split_suffix is None
        and job.split_suffix is not None
    ):
        return CandidateReason.orphan_after_recombine

    return CandidateReason.orphan_other


def detect_supersession_candidates(
    session: Session,
    batch: ImportBatch,
) -> CandidateDelta:
    """Persist supersession candidates for orphaned jobs in a live ingest.

    Pre:  batch.sheet_kind is set.
          Stage 5 has finished; resolved_job_id is populated for every
          staging row whose processing_status == ImportStatus.processed.
          session is inside the same outer transaction as Stage 5.
    Post: When batch.sheet_kind != SheetKind.live: returns
          CandidateDelta.empty() and writes nothing.
          When batch.sheet_kind == SheetKind.live: opens new candidates
          for unshipped active jobs that disappeared from this batch and
          auto-resolves prior pending candidates whose job reappeared.
    Raises: propagates any DB-layer error; no application-level exceptions.
    """
    if batch.sheet_kind != SheetKind.live:
        return CandidateDelta.empty()

    # Collect (assembly_id, job_id) pairs for every processed row in this batch.
    referenced_pairs = session.execute(
        select(Job.assembly_id, Job.id)
        .join(ImportStagingRow, ImportStagingRow.resolved_job_id == Job.id)
        .where(ImportStagingRow.batch_id == batch.id)
        .where(ImportStagingRow.processing_status == ImportStatus.processed)
        .where(ImportStagingRow.resolved_job_id.is_not(None))
    ).all()

    touched_assembly_ids: set[int] = {row.assembly_id for row in referenced_pairs}
    referenced_job_ids: set[int] = {row.id for row in referenced_pairs}

    # Auto-return prior pending candidates whose job reappeared in this batch.
    auto_returned: list[int] = []
    if referenced_job_ids:
        candidates_to_close = session.scalars(
            select(JobSupersessionCandidate)
            .where(JobSupersessionCandidate.resolved_at.is_(None))
            .where(JobSupersessionCandidate.job_id.in_(list(referenced_job_ids)))
        ).all()
        for cand in candidates_to_close:
            cand.resolved_at = _now_utc()
            cand.resolution = CandidateResolution.auto_returned
            auto_returned.append(cand.id)

    if not touched_assembly_ids:
        return CandidateDelta(
            new_pending_candidate_ids=(),
            auto_returned_candidate_ids=tuple(auto_returned),
            skipped_shipped_job_ids=(),
            skipped_already_pending_job_ids=(),
            touched_assembly_ids=(),
        )

    # Find active, unshipped jobs in touched assemblies that are NOT referenced.
    candidate_jobs = session.scalars(
        select(Job)
        .where(Job.assembly_id.in_(list(touched_assembly_ids)))
        .where(Job.superseded_at.is_(None))
        .where(Job.shipped_at.is_(None))
        .where(Job.id.not_in(list(referenced_job_ids)))
    ).all()

    # Build a set of job_ids that already have a pending candidate.
    pending_index: set[int] = set(
        session.scalars(
            select(JobSupersessionCandidate.job_id)
            .where(JobSupersessionCandidate.resolved_at.is_(None))
        ).all()
    )

    new_pending: list[int] = []
    skipped_shipped: list[int] = []
    skipped_already_pending: list[int] = []

    for job in candidate_jobs:
        if job.id in pending_index:
            skipped_already_pending.append(job.id)
            continue
        if _has_any_shipped_history(session, job):
            # Defence — should be unreachable given the shipped_at IS NULL
            # filter above; guards against invariant violations elsewhere.
            skipped_shipped.append(job.id)
            continue

        cand = JobSupersessionCandidate(
            job_id=job.id,
            detected_in_batch_id=batch.id,
            reason=_infer_reason(session, job, referenced_job_ids),
            detected_at=_now_utc(),
        )
        session.add(cand)
        session.flush()
        new_pending.append(cand.id)

    log.info(
        "supersession.detect.delta",
        extra={
            "batch_id": batch.id,
            "new_pending": len(new_pending),
            "auto_returned": len(auto_returned),
            "skipped_shipped": len(skipped_shipped),
            "skipped_pending": len(skipped_already_pending),
        },
    )

    return CandidateDelta(
        new_pending_candidate_ids=tuple(new_pending),
        auto_returned_candidate_ids=tuple(auto_returned),
        skipped_shipped_job_ids=tuple(skipped_shipped),
        skipped_already_pending_job_ids=tuple(skipped_already_pending),
        touched_assembly_ids=tuple(touched_assembly_ids),
    )


# ---------------------------------------------------------------------------
# Resolution (Epoch 4)
# ---------------------------------------------------------------------------


class CandidateClosedError(Exception):
    """Raised when an operation targets a candidate that is already resolved."""

    def __init__(self, candidate_id: int, current_resolution: CandidateResolution) -> None:
        super().__init__(
            f"Candidate {candidate_id} is already resolved as {current_resolution.value!r}"
        )
        self.candidate_id = candidate_id
        self.current_resolution = current_resolution


@dataclass(frozen=True)
class ApplyOutcome:
    """Structured result from the flush-only approval core."""

    kind: str  # "approved" | "shield_closed_as_reject"
    shield_reason: str | None


@dataclass(frozen=True)
class BulkApprovalResult:
    """Aggregate outcome of a bulk-approve operation."""

    approved: tuple[int, ...]
    shield_rejected: tuple[int, ...]
    already_closed: tuple[int, ...]
    not_found: tuple[int, ...]


def list_candidates(
    session: Session,
    *,
    status: str,
    resolution: CandidateResolution | None,
    limit: int,
    offset: int,
) -> tuple[list[JobSupersessionCandidate], int]:
    """Page across candidate rows for the reconciliation pane.

    Pre:  status in {"pending", "resolved", "all"}.
          resolution in {None, CandidateResolution.*}.
          limit > 0; offset >= 0.
          Callers must not combine status=="pending" with resolution!=None
          (the API layer enforces this with 422).
    Post: Returns (rows, total) ordered by detected_at DESC, id DESC.
    Raises: never (validation is done in the API layer).
    """
    from sqlalchemy import func

    base = select(JobSupersessionCandidate)

    if status == "pending":
        base = base.where(JobSupersessionCandidate.resolved_at.is_(None))
    elif status == "resolved":
        base = base.where(JobSupersessionCandidate.resolved_at.is_not(None))
        if resolution is not None:
            base = base.where(JobSupersessionCandidate.resolution == resolution)
    else:  # "all"
        if resolution is not None:
            base = base.where(JobSupersessionCandidate.resolution == resolution)

    count_q = select(func.count()).select_from(base.subquery())
    total: int = session.scalar(count_q) or 0

    rows = session.scalars(
        base.order_by(
            JobSupersessionCandidate.detected_at.desc(),
            JobSupersessionCandidate.id.desc(),
        )
        .limit(limit)
        .offset(offset)
    ).all()

    return list(rows), total


def _apply_approval(
    session: Session,
    candidate: JobSupersessionCandidate,
) -> ApplyOutcome:
    """Flush-only core of approval.  Does not commit.

    Pre:  candidate is attached to session.
          candidate.resolved_at IS NULL.
    Post: Shield re-checked.  Either approves (flips Job) or rejects via
          shield (closes candidate as REJECT, leaves Job unchanged).
    Raises: CandidateClosedError when candidate.resolved_at IS NOT NULL on entry.
    """
    if candidate.resolved_at is not None:
        raise CandidateClosedError(candidate.id, candidate.resolution)

    job: Job = session.get(Job, candidate.job_id)  # type: ignore[assignment]

    if _has_any_shipped_history(session, job):
        candidate.resolved_at = _now_utc()
        candidate.resolution = CandidateResolution.reject
        candidate.closed_by_shield_reason = "shipped_at_set"
        log.warning(
            "supersession.shield_tripped",
            extra={
                "candidate_id": candidate.id,
                "job_id": candidate.job_id,
                "detected_in_batch_id": candidate.detected_in_batch_id,
                "shield_reason": "shipped_at_set",
            },
        )
        return ApplyOutcome(kind="shield_closed_as_reject", shield_reason="shipped_at_set")

    candidate.resolved_at = _now_utc()
    candidate.resolution = CandidateResolution.approve
    job.superseded_at = _now_utc()
    job.superseded_by_batch_id = candidate.detected_in_batch_id
    return ApplyOutcome(kind="approved", shield_reason=None)


def _apply_rejection(
    session: Session,
    candidate: JobSupersessionCandidate,
) -> None:
    """Flush-only core of rejection.  Does not commit.

    Pre:  candidate is attached to session.
          candidate.resolved_at IS NULL.
    Post: candidate closed as REJECT; Job is NOT mutated.
    Raises: CandidateClosedError when candidate.resolved_at IS NOT NULL on entry.
    """
    if candidate.resolved_at is not None:
        raise CandidateClosedError(candidate.id, candidate.resolution)

    candidate.resolved_at = _now_utc()
    candidate.resolution = CandidateResolution.reject


def approve_candidate(
    session: Session,
    candidate_id: int,
) -> JobSupersessionCandidate:
    """HTTP-shaped wrapper: load, apply, commit, return.

    Pre:  candidate_id is the candidate's primary key.
    Post: On success: _apply_approval ran, session.commit() ran, returns candidate.
          On shield-trip: candidate resolved as REJECT, session.commit() ran,
          returns candidate (HTTP layer returns 200 with closed_by_shield_reason).
    Raises: CandidateClosedError when candidate.resolved_at IS NOT NULL.
            Caller (API layer) translates to 409.
    """
    candidate: JobSupersessionCandidate | None = session.get(
        JobSupersessionCandidate, candidate_id
    )
    if candidate is None:
        raise KeyError(candidate_id)

    _apply_approval(session, candidate)
    session.commit()
    return candidate


def reject_candidate(
    session: Session,
    candidate_id: int,
) -> JobSupersessionCandidate:
    """Close the candidate without touching the Job.

    Pre:  candidate exists.
    Post: candidate.resolved_at = now_utc(); resolution = REJECT; session.commit().
    Raises: CandidateClosedError when candidate.resolved_at IS NOT NULL.
    """
    candidate: JobSupersessionCandidate | None = session.get(
        JobSupersessionCandidate, candidate_id
    )
    if candidate is None:
        raise KeyError(candidate_id)

    _apply_rejection(session, candidate)
    session.commit()
    return candidate


def bulk_approve_candidates(
    session: Session,
    candidate_ids: list[int],
) -> BulkApprovalResult:
    """Best-effort approval of many candidates via per-candidate SAVEPOINTs.

    Pre:  candidate_ids is non-empty.
          session may have an open transaction at entry.
    Post: Each candidate processed in its own savepoint.  Shield trip on one
          does not block the others.  session.commit() called once at the end.
    Raises: never; all outcomes are captured in BulkApprovalResult.
    """
    # Deduplicate while preserving order (contract: server-side dedup).
    seen: set[int] = set()
    unique_ids: list[int] = []
    for cid in candidate_ids:
        if cid not in seen:
            seen.add(cid)
            unique_ids.append(cid)

    approved: list[int] = []
    shield_rejected: list[int] = []
    already_closed: list[int] = []
    not_found: list[int] = []

    for cid in unique_ids:
        nested = session.begin_nested()
        try:
            candidate: JobSupersessionCandidate | None = session.get(
                JobSupersessionCandidate, cid
            )
            if candidate is None:
                not_found.append(cid)
                nested.rollback()
                continue
            outcome = _apply_approval(session, candidate)
            if outcome.kind == "approved":
                approved.append(cid)
            else:
                shield_rejected.append(cid)
            nested.commit()
        except CandidateClosedError:
            log.info(
                "supersession.already_closed",
                extra={
                    "candidate_id": cid,
                    "caller": "bulk",
                },
            )
            already_closed.append(cid)
            nested.rollback()

    log.info(
        "supersession.bulk.summary",
        extra={
            "requested": len(unique_ids),
            "approved": len(approved),
            "shield_rejected": len(shield_rejected),
            "already_closed": len(already_closed),
            "not_found": len(not_found),
        },
    )

    session.commit()
    return BulkApprovalResult(
        approved=tuple(approved),
        shield_rejected=tuple(shield_rejected),
        already_closed=tuple(already_closed),
        not_found=tuple(not_found),
    )
