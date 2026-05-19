"""410 Gone handlers for supersession endpoints removed in Phase 18c.

# DELETE after follow-up ticket — one production release has passed since
# Phase 3 landed and no stale client is still calling these paths.
# When the ticket closes, remove this module and its router registration
# from backend/app/api/__init__.py.

These routes preserve the exact paths of the deleted supersession.py
endpoints so that stale clients receive a structured 410 rather than a 404.
"""
from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import JSONResponse

router = APIRouter()

_GONE_DETAIL = "The supersession candidate workflow was removed in Phase 18c."
_REMOVED_IN = "Phase 18c"


def _gone() -> JSONResponse:
    return JSONResponse(
        status_code=410,
        content={"detail": _GONE_DETAIL, "removed_in": _REMOVED_IN},
    )


@router.get("/supersession-candidates")
def get_supersession_candidates() -> JSONResponse:
    return _gone()


@router.post("/supersession-candidates/bulk-approve")
def bulk_approve() -> JSONResponse:
    return _gone()


@router.post("/supersession-candidates/{candidate_id}/approve")
def approve(candidate_id: int) -> JSONResponse:  # noqa: ARG001
    return _gone()


@router.post("/supersession-candidates/{candidate_id}/reject")
def reject(candidate_id: int) -> JSONResponse:  # noqa: ARG001
    return _gone()
