from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy.orm import Session

from ..models import BuildType, JobStatus
from ..schemas import JobReadExpanded
from ..services import jobs as jobs_service
from .deps import HistoryPageParams, PageParams, get_pagination, get_session

router = APIRouter()


@router.get("", response_model=list[JobReadExpanded])
def list_jobs(
    response: Response,
    status: str | None = Query(default=None),
    assembly_id: int | None = Query(default=None),
    customer_id: int | None = Query(default=None),
    build_type: BuildType | None = Query(default=None),
    page: PageParams = Depends(get_pagination),
    session: Session = Depends(get_session),
):
    status_filter = (
        [JobStatus(v.strip()) for v in status.split(",")] if status is not None else None
    )
    rows, total = jobs_service.list_jobs(
        session,
        status_filter=status_filter,
        assembly_id=assembly_id,
        customer_id=customer_id,
        build_type=build_type,
        limit=page.limit,
        offset=page.offset,
    )
    response.headers["X-Total-Count"] = str(total)
    return rows


@router.get("/shipping", response_model=list[JobReadExpanded])
def list_shipping_jobs(
    response: Response,
    page: PageParams = Depends(get_pagination),
    session: Session = Depends(get_session),
):
    rows, total = jobs_service.list_shipping(session, limit=page.limit, offset=page.offset)
    response.headers["X-Total-Count"] = str(total)
    return rows


@router.get("/history", response_model=list[JobReadExpanded])
def list_job_history(
    response: Response,
    page: HistoryPageParams = Depends(),
    session: Session = Depends(get_session),
):
    rows, total = jobs_service.list_history(
        session, search=page.search, limit=page.limit, offset=page.offset,
    )
    response.headers["X-Total-Count"] = str(total)
    return rows


@router.get("/{job_id}", response_model=JobReadExpanded)
def get_job(job_id: int, session: Session = Depends(get_session)):
    job = jobs_service.get_job(session, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


@router.get("/{job_id}/lineage", response_model=list[JobReadExpanded])
def get_job_lineage(job_id: int, session: Session = Depends(get_session)):
    rows = jobs_service.get_lineage(session, job_id)
    if rows is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return rows
