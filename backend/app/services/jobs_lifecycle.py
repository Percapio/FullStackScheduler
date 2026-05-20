"""Lifecycle operations for Job supersession.

Epoch 2: shipped-history shield.
Resolution services are in Epoch 4.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import (
    CandidateResolution,
    Job,
    JobSupersessionCandidate,
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
    Post: Returns True iff job.ever_shipped_at IS NOT NULL.
          Returns False otherwise.
    Raises: never.

    Invariant (INV-S1, INV-S3 — Phase 16): ever_shipped_at is set atomically
    with the first transition to JobStatus.shipped (in transform._apply_shipped)
    and is never cleared thereafter.  Therefore ever_shipped_at IS NOT NULL is
    a complete proxy for "has shipped at least once in its persisted lifetime."
    shipped_at may now be NULL for un-shipped jobs (Phase 16 §3.4).
    """
    return job.ever_shipped_at is not None


# ---------------------------------------------------------------------------
# Candidate detection (Epoch 3) — deleted in Phase 20
# ---------------------------------------------------------------------------


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
        candidate.closed_by_shield_reason = "ever_shipped"
        log.warning(
            "supersession.shield_tripped",
            extra={
                "candidate_id": candidate.id,
                "job_id": candidate.job_id,
                "detected_in_batch_id": candidate.detected_in_batch_id,
                "shield_reason": "ever_shipped",
            },
        )
        return ApplyOutcome(kind="shield_closed_as_reject", shield_reason="ever_shipped")

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
