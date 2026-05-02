from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from ..models import Assembly, Job, JobStatus


def list_assemblies(
    session: Session, *, customer_id: int | None, limit: int, offset: int,
) -> tuple[list[Assembly], int]:
    base = select(Assembly)
    if customer_id is not None:
        base = base.where(
            Assembly.id.in_(
                select(Job.assembly_id).where(Job.customer_id == customer_id)
            )
        )
    total = session.scalar(select(func.count()).select_from(base.subquery()))
    rows = session.scalars(
        base.options(selectinload(Assembly.classifications))
        .order_by(Assembly.id)
        .limit(limit)
        .offset(offset)
    ).unique().all()
    return list(rows), total


def get_assembly(session: Session, assembly_id: int) -> Assembly | None:
    return session.execute(
        select(Assembly)
        .where(Assembly.id == assembly_id)
        .options(selectinload(Assembly.classifications))
    ).unique().scalar_one_or_none()


def get_assembly_jobs(
    session: Session,
    assembly_id: int,
    *,
    status_filter: list[JobStatus] | None,
) -> tuple[Assembly, list[Job]] | None:
    assembly = get_assembly(session, assembly_id)
    if assembly is None:
        return None
    job_query = select(Job).where(Job.assembly_id == assembly_id)
    if status_filter is not None:
        job_query = job_query.where(Job.status.in_(status_filter))
    jobs = list(session.scalars(job_query.order_by(Job.id)).all())
    return assembly, jobs
