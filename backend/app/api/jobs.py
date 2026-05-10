from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy.orm import Session

from ..models import BuildType, JobStatus
from ..schemas import HistoryJobEditRequest, JobDiscardRequest, JobReadExpanded, JobRestoreRequest, RestoreConflictPreview
from ..services import jobs as jobs_service
from .deps import HistoryPageParams, PageParams, get_pagination, get_session, ErroredPageParams

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


# NOTE: /discarded MUST be declared before /{job_id} to avoid FastAPI
# consuming the literal string "discarded" as an integer job_id.
@router.get("/discarded", response_model=list[JobReadExpanded])
def list_discarded_jobs(
    response: Response,
    page: ErroredPageParams = Depends(ErroredPageParams),
    session: Session = Depends(get_session),
):
    rows, total = jobs_service.list_discarded_jobs(
        session, limit=page.limit, offset=page.offset, search=page.search,
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


@router.post("/{job_id}/discard", response_model=JobReadExpanded)
def discard_job(job_id: int, body: JobDiscardRequest, session: Session = Depends(get_session)):
    """Soft-delete a job by setting discarded_at.

    Returns the soft-deleted job on success.
    404 if the job does not exist.
    409 if the job is already discarded (body contains the existing row).
    """
    try:
        job = jobs_service.discard_job(session, job_id, body.reason)
    except jobs_service.JobDiscardError as exc:
        msg = str(exc)
        if msg.startswith("not found"):
            raise HTTPException(status_code=404, detail="Job not found") from exc
        if msg.startswith("already discarded"):
            existing = jobs_service.get_job_including_discarded(session, job_id)
            raise HTTPException(
                status_code=409,
                detail={"detail": "Job is already discarded", "job": JobReadExpanded.model_validate(existing).model_dump(mode="json")},
            ) from exc
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    session.commit()
    return job


@router.get("/{job_id}/restore-preview", response_model=RestoreConflictPreview)
def get_job_restore_preview(job_id: int, session: Session = Depends(get_session)):
    """Return a conflict-preview snapshot for a discarded job.

    404 if the job does not exist.
    409 if the job is not currently discarded.
    """
    try:
        return jobs_service.preview_restore_job(session, job_id)
    except jobs_service.JobRestoreError as exc:
        msg = str(exc)
        if msg.startswith("not found"):
            raise HTTPException(status_code=404, detail="Job not found") from exc
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/{job_id}/restore", response_model=JobReadExpanded)
def restore_job(
    job_id: int,
    body: JobRestoreRequest | None = None,
    session: Session = Depends(get_session),
):
    """Restore a discarded job, applying staging-side resolution actions atomically.

    404 if the job does not exist.
    409 if the job is not currently discarded.
    409 with body { detail, preview } on residual collision after actions.
    422 with body { detail, action_index } on per-action validation failure.
    """
    if body is None:
        body = JobRestoreRequest()
    try:
        job = jobs_service.restore_job_with_actions(session, job_id, body.actions)
    except jobs_service.JobRestoreError as exc:
        msg = str(exc)
        if msg.startswith("not found"):
            raise HTTPException(status_code=404, detail="Job not found") from exc
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception as exc:
        # Import here to avoid circular import at module level.
        from ..services.staging import StagingRestoreValidationError
        from ..services.jobs import JobRestoreConflictError
        if isinstance(exc, StagingRestoreValidationError):
            raise HTTPException(
                status_code=422,
                detail={"message": exc.detail, "action_index": exc.action_index},
            ) from exc
        if isinstance(exc, JobRestoreConflictError):
            raise HTTPException(
                status_code=409,
                detail={
                    "message": "Residual collision after applying actions.",
                    "preview": exc.preview.model_dump(mode="json"),
                },
            ) from exc
        raise
    return job


@router.patch("/{job_id}/history-edit", response_model=JobReadExpanded)
def edit_history_job(
    job_id: int,
    body: HistoryJobEditRequest,
    session: Session = Depends(get_session),
):
    """Edit reconciliation-style fields of a shipped job.

    404 if job not found.
    409 if not shipped or already discarded (body: { kind }).
    409 if the edit would create an identity collision
        (body: { message, colliding_job_id }).
    422 with body { field, message } on per-field parse failure.
    422 (Pydantic) if no raw_* field was provided.
    """
    try:
        job = jobs_service.edit_history_job(session, job_id, body)
    except jobs_service.JobEditNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Job not found") from exc
    except jobs_service.JobEditNotEditableError as exc:
        raise HTTPException(
            status_code=409,
            detail={"message": "Job is not editable in History", "kind": exc.kind},
        ) from exc
    except jobs_service.JobEditValidationError as exc:
        raise HTTPException(
            status_code=422,
            detail={"field": exc.field, "message": exc.message},
        ) from exc
    except jobs_service.JobEditIdentityCollisionError as exc:
        raise HTTPException(
            status_code=409,
            detail={"message": "Identity collision", "colliding_job_id": exc.colliding_job_id},
        ) from exc
    session.commit()
    return job
