from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy.orm import Session

from ..models import JobStatus
from ..schemas import AssemblyRead, AssemblyWithJobs, JobRead
from ..services import assemblies as assemblies_service
from .deps import PageParams, get_pagination, get_session

router = APIRouter()


@router.get("", response_model=list[AssemblyRead])
def list_assemblies(
    response: Response,
    customer_id: int | None = Query(default=None),
    page: PageParams = Depends(get_pagination),
    session: Session = Depends(get_session),
):
    rows, total = assemblies_service.list_assemblies(
        session, customer_id=customer_id, limit=page.limit, offset=page.offset,
    )
    response.headers["X-Total-Count"] = str(total)
    return rows


@router.get("/{assembly_id}", response_model=AssemblyRead)
def get_assembly(assembly_id: int, session: Session = Depends(get_session)):
    assembly = assemblies_service.get_assembly(session, assembly_id)
    if assembly is None:
        raise HTTPException(status_code=404, detail="Assembly not found")
    return assembly


@router.get("/{assembly_id}/jobs", response_model=AssemblyWithJobs)
def get_assembly_jobs(
    assembly_id: int,
    status: str | None = Query(default=None),
    session: Session = Depends(get_session),
):
    status_filter = (
        [JobStatus(v.strip()) for v in status.split(",")] if status is not None else None
    )
    result = assemblies_service.get_assembly_jobs(
        session, assembly_id, status_filter=status_filter,
    )
    if result is None:
        raise HTTPException(status_code=404, detail="Assembly not found")
    assembly, jobs = result
    data = AssemblyRead.model_validate(assembly).model_dump()
    data["jobs"] = [JobRead.model_validate(j) for j in jobs]
    return data
