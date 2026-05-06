"""API endpoints for supersession candidate listing and resolution.

Routes are mounted under /api/staging so they live alongside the other
staging-management endpoints.  Literal-path routes (bulk-approve) are
declared before the parameterised `/{id}/…` routes so FastAPI does not
parse them as integer IDs.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from ..models import CandidateResolution
from ..schemas import (
    BulkApprovalResultRead,
    JobSupersessionCandidatePage,
    JobSupersessionCandidateRead,
    SupersessionBulkApprovalRequest,
)
from ..services.jobs_lifecycle import (
    BulkApprovalResult,
    CandidateClosedError,
    approve_candidate,
    bulk_approve_candidates,
    list_candidates,
    reject_candidate,
)
from .deps import get_session

log = logging.getLogger(__name__)

router = APIRouter()

# ---------------------------------------------------------------------------
# GET /staging/supersession-candidates
# ---------------------------------------------------------------------------

_VALID_STATUS = {"pending", "resolved", "all"}
_RESOLUTION_MAP: dict[str, CandidateResolution] = {
    "approve": CandidateResolution.approve,
    "reject": CandidateResolution.reject,
    "auto_returned": CandidateResolution.auto_returned,
}


@router.get("/supersession-candidates", response_model=JobSupersessionCandidatePage)
def list_supersession_candidates(
    status: str = Query(default="pending"),
    resolution: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    session: Session = Depends(get_session),
) -> JobSupersessionCandidatePage:
    if status not in _VALID_STATUS:
        raise HTTPException(
            status_code=422,
            detail=f"Invalid status {status!r}. Must be one of: pending, resolved, all.",
        )

    resolved_resolution: CandidateResolution | None = None
    if resolution is not None:
        if resolution not in _RESOLUTION_MAP:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"Invalid resolution {resolution!r}. "
                    "Must be one of: approve, reject, auto_returned."
                ),
            )
        resolved_resolution = _RESOLUTION_MAP[resolution]

    if status == "pending" and resolved_resolution is not None:
        raise HTTPException(
            status_code=422,
            detail="Cannot combine status=pending with a resolution filter.",
        )

    rows, total = list_candidates(
        session,
        status=status,
        resolution=resolved_resolution,
        limit=limit,
        offset=offset,
    )
    return JobSupersessionCandidatePage(items=rows, total=total)


# ---------------------------------------------------------------------------
# POST /staging/supersession-candidates/bulk-approve
# (must precede /{id}/approve so FastAPI does not match "bulk-approve" as int)
# ---------------------------------------------------------------------------


@router.post(
    "/supersession-candidates/bulk-approve",
    response_model=BulkApprovalResultRead,
)
def bulk_approve(
    body: SupersessionBulkApprovalRequest,
    session: Session = Depends(get_session),
) -> BulkApprovalResultRead:
    result: BulkApprovalResult = bulk_approve_candidates(session, body.ids)
    return BulkApprovalResultRead(
        approved=list(result.approved),
        shield_rejected=list(result.shield_rejected),
        already_closed=list(result.already_closed),
        not_found=list(result.not_found),
    )


# ---------------------------------------------------------------------------
# POST /staging/supersession-candidates/{id}/approve
# ---------------------------------------------------------------------------


@router.post(
    "/supersession-candidates/{candidate_id}/approve",
    response_model=JobSupersessionCandidateRead,
)
def approve(
    candidate_id: int,
    session: Session = Depends(get_session),
) -> JobSupersessionCandidateRead:
    try:
        candidate = approve_candidate(session, candidate_id)
    except KeyError:
        raise HTTPException(status_code=404, detail={"code": "not_found"})
    except CandidateClosedError as exc:
        log.info(
            "supersession.already_closed",
            extra={
                "candidate_id": exc.candidate_id,
                "current_resolution": exc.current_resolution.value,
                "caller": "single",
            },
        )
        raise HTTPException(
            status_code=409,
            detail={
                "code": "already_closed",
                "current_resolution": exc.current_resolution.value,
            },
        )
    return candidate  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# POST /staging/supersession-candidates/{id}/reject
# ---------------------------------------------------------------------------


@router.post(
    "/supersession-candidates/{candidate_id}/reject",
    response_model=JobSupersessionCandidateRead,
)
def reject(
    candidate_id: int,
    session: Session = Depends(get_session),
) -> JobSupersessionCandidateRead:
    try:
        candidate = reject_candidate(session, candidate_id)
    except KeyError:
        raise HTTPException(status_code=404, detail={"code": "not_found"})
    except CandidateClosedError as exc:
        log.info(
            "supersession.already_closed",
            extra={
                "candidate_id": exc.candidate_id,
                "current_resolution": exc.current_resolution.value,
                "caller": "single",
            },
        )
        raise HTTPException(
            status_code=409,
            detail={
                "code": "already_closed",
                "current_resolution": exc.current_resolution.value,
            },
        )
    return candidate  # type: ignore[return-value]
