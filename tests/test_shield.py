"""_has_any_shipped_history — 100% branch coverage (Epoch 2).

The shield predicate classifies a Job as shipping history (and therefore
ineligible for supersession) based solely on job.shipped_at.
"""
from datetime import date

from backend.app.models import Assembly, BuildType, Customer, Job, JobStatus
from backend.app.services.jobs_lifecycle import _has_any_shipped_history


def _make_job(session, *, shipped_at=None):
    customer = Customer(name=f"Cust-{id(session)}-{shipped_at}")
    session.add(customer)
    session.flush()
    asm = Assembly(part_number=f"SHIELD-{id(session)}-{shipped_at}")
    session.add(asm)
    session.flush()
    job = Job(
        assembly_id=asm.id,
        customer_id=customer.id,
        quantity=1,
        build_type=BuildType.new,
        shipped_at=shipped_at,
        status=JobStatus.shipped if shipped_at is not None else JobStatus.planned,
    )
    session.add(job)
    session.flush()
    return job


def test_has_any_shipped_history_returns_false_when_shipped_at_is_null(session):
    job = _make_job(session, shipped_at=None)
    assert _has_any_shipped_history(session, job) is False


def test_has_any_shipped_history_returns_true_when_shipped_at_is_set(session):
    job = _make_job(session, shipped_at=date(2026, 4, 1))
    assert _has_any_shipped_history(session, job) is True
