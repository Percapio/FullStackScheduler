from __future__ import annotations

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, selectinload

from ..models import Assembly, BuildType, Customer, Job, JobStatus

JOB_EXPAND_OPTIONS = (
    selectinload(Job.assembly).selectinload(Assembly.classifications),
    selectinload(Job.customer),
    selectinload(Job.salesperson),
)


def _count(session: Session, base) -> int:
    return session.scalar(select(func.count()).select_from(base.subquery()))


def _active_jobs_base() -> select:
    """Return a base SELECT over Jobs that are not superseded.

    All active-job surfaces (list_jobs, list_shipping, get_job) must compose
    from this helper so the superseded_at IS NULL filter is applied uniformly.
    History / lineage queries use select(Job) directly and document the omission.
    """
    return select(Job).where(Job.superseded_at.is_(None))


def list_jobs(
    session: Session,
    *,
    status_filter: list[JobStatus] | None,
    assembly_id: int | None,
    customer_id: int | None,
    build_type: BuildType | None,
    limit: int,
    offset: int,
) -> tuple[list[Job], int]:
    base = _active_jobs_base()
    if status_filter is not None:
        base = base.where(Job.status.in_(status_filter))
    if assembly_id is not None:
        base = base.where(Job.assembly_id == assembly_id)
    if customer_id is not None:
        base = base.where(Job.customer_id == customer_id)
    if build_type is not None:
        base = base.where(Job.build_type == build_type)

    total = _count(session, base)
    rows = session.scalars(
        base.options(*JOB_EXPAND_OPTIONS)
        .order_by(Job.shipped_at.asc().nullslast(), Job.id.asc())
        .limit(limit)
        .offset(offset)
    ).unique().all()
    return list(rows), total


def list_shipping(
    session: Session, *, limit: int, offset: int,
) -> tuple[list[Job], int]:
    base = _active_jobs_base().where(Job.status != JobStatus.shipped)
    total = _count(session, base)
    rows = session.scalars(
        base.options(*JOB_EXPAND_OPTIONS)
        .order_by(Job.resolved_ship_date.asc().nullslast(), Job.id.asc())
        .limit(limit)
        .offset(offset)
    ).unique().all()
    return list(rows), total


def list_history(
    session: Session,
    *,
    search: str | None,
    limit: int,
    offset: int,
    include_superseded: bool = True,
) -> tuple[list[Job], int]:
    # History is the audit trail; superseded rows belong here by default (TDD §1 Epoch 1).
    base = select(Job).where(Job.status == JobStatus.shipped)
    if not include_superseded:
        base = base.where(Job.superseded_at.is_(None))
    if search:
        pattern = f"%{search}%"
        base = (
            base.join(Job.assembly)
            .join(Job.customer)
            .where(
                or_(
                    Assembly.part_number.ilike(pattern),
                    Job.split_suffix.ilike(pattern),
                    Job.repeat_reference.ilike(pattern),
                    Customer.name.ilike(pattern),
                    Assembly.base_mfg_notes.ilike(pattern),
                )
            )
        )
    total = _count(session, base)
    rows = session.scalars(
        base.options(*JOB_EXPAND_OPTIONS)
        .order_by(Job.shipped_at.desc().nullslast(), Job.id.desc())
        .limit(limit)
        .offset(offset)
    ).unique().all()
    return list(rows), total


def get_job(session: Session, job_id: int) -> Job | None:
    return session.execute(
        _active_jobs_base()
        .where(Job.id == job_id)
        .options(*JOB_EXPAND_OPTIONS)
    ).unique().scalar_one_or_none()


def get_lineage(session: Session, job_id: int, *, include_superseded: bool = True) -> list[Job] | None:
    # Lineage is an audit surface; superseded rows are included by default (TDD §1 Epoch 1).
    anchor = session.execute(
        select(Job.repeat_reference, Assembly.part_number)
        .join(Assembly, Assembly.id == Job.assembly_id)
        .where(Job.id == job_id)
    ).one_or_none()
    if anchor is None:
        return None

    anchor_repeat_ref, anchor_part_number = anchor
    related_part_numbers: set[str] = {anchor_part_number}
    if anchor_repeat_ref:
        related_part_numbers.add(anchor_repeat_ref)

    chronology = func.coalesce(
        Job.shipped_at, Job.resolved_ship_date, func.date(Job.created_at)
    )
    base = (
        select(Job)
        .join(Assembly, Assembly.id == Job.assembly_id)
        .where(
            or_(
                Assembly.part_number.in_(related_part_numbers),
                Job.repeat_reference.in_(related_part_numbers),
            )
        )
    )
    if not include_superseded:
        base = base.where(Job.superseded_at.is_(None))
    rows = session.scalars(
        base.options(*JOB_EXPAND_OPTIONS)
        .order_by(chronology.asc(), Job.id.asc())
    ).unique().all()
    return list(rows)
