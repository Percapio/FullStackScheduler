from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy.orm import Session

from ..models import ImportStatus
from ..schemas import (
    ImportStagingRowRead,
    JobReadExpanded,
    StagingRowCorrectionRequest,
    StagingRowDetailRead,
)
from ..services import staging as staging_service
from .deps import PageParams, get_pagination, get_session

router = APIRouter()


@router.get("/errored", response_model=list[ImportStagingRowRead])
def list_errored(
    response: Response,
    page: PageParams = Depends(get_pagination),
    session: Session = Depends(get_session),
):
    rows, total = staging_service.list_errored(session, limit=page.limit, offset=page.offset)
    response.headers["X-Total-Count"] = str(total)
    return rows


@router.get("/{row_id}", response_model=StagingRowDetailRead)
def get_staging_row(row_id: int, session: Session = Depends(get_session)):
    row = staging_service.get_staging_row(session, row_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Staging row not found")
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

    job = staging_service.apply_correction(
        session, row, payload.model_dump(exclude_unset=True),
    )
    if job is None:
        raise HTTPException(status_code=422, detail=row.processing_error)
    return job
