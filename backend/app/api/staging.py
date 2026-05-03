from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy.orm import Session

from ..models import ImportStatus
from ..schemas import (
    ConflictGroup,
    ImportStagingRowRead,
    JobReadExpanded,
    StagingRowCorrectionRequest,
    StagingRowDetailRead,
)
from ..services import staging as staging_service
from .deps import PageParams, get_pagination, get_session

router = APIRouter()


# NOTE: literal-path routes (`/errored`, `/discarded`, and Phase 3's `/conflicts`)
# MUST be declared above the catch-all `GET /{row_id}` so FastAPI does not match
# them as `int(row_id)` and raise 422.


@router.get("/conflicts", response_model=list[ConflictGroup])
def list_conflicts(session: Session = Depends(get_session)):
    return staging_service.list_conflicts(session)


@router.get("/errored", response_model=list[ImportStagingRowRead])
def list_errored(
    response: Response,
    page: PageParams = Depends(get_pagination),
    session: Session = Depends(get_session),
):
    rows, total = staging_service.list_errored(session, limit=page.limit, offset=page.offset)
    response.headers["X-Total-Count"] = str(total)
    return rows


@router.get("/discarded", response_model=list[ImportStagingRowRead])
def list_discarded(
    response: Response,
    page: PageParams = Depends(get_pagination),
    session: Session = Depends(get_session),
):
    rows, total = staging_service.list_discarded(
        session, limit=page.limit, offset=page.offset,
    )
    response.headers["X-Total-Count"] = str(total)
    return rows


@router.get("/{row_id}", response_model=StagingRowDetailRead)
def get_staging_row(row_id: int, session: Session = Depends(get_session)):
    row = staging_service.get_staging_row(session, row_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Staging row not found")
    return row


@router.delete("/{row_id}", status_code=204, response_class=Response)
def discard_staging_row(row_id: int, session: Session = Depends(get_session)):
    row = staging_service.get_staging_row(session, row_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Staging row not found")
    try:
        staging_service.discard_staging_row(session, row)
    except staging_service.StagingDiscardError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return Response(status_code=204)


@router.post("/{row_id}/restore", response_model=ImportStagingRowRead)
def restore_staging_row(row_id: int, session: Session = Depends(get_session)):
    row = staging_service.get_staging_row(session, row_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Staging row not found")
    try:
        staging_service.restore_staging_row(session, row)
    except staging_service.StagingRestoreError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return row


@router.post("/{row_id}/correct", response_model=JobReadExpanded)
def correct_staging_row(
    row_id: int,
    payload: StagingRowCorrectionRequest,
    session: Session = Depends(get_session),
):
    row = staging_service.get_staging_row(session, row_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Staging row not found")

    if row.processing_status != ImportStatus.error:
        raise HTTPException(
            status_code=409,
            detail=f"Row must be in error state to correct; got '{row.processing_status.value}'",
        )

    if row.discarded_at is not None:
        raise HTTPException(
            status_code=409,
            detail="Row has been discarded; restore it before submitting a correction.",
        )

    job = staging_service.apply_correction(
        session, row, payload.model_dump(exclude_unset=True),
    )
    if job is None:
        raise HTTPException(status_code=422, detail=row.processing_error)
    return job
