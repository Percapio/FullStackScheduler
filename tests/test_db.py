from datetime import date

from sqlalchemy import select

from backend.app.models import Assembly, Customer, Job, JobStatus


def test_date_columns_roundtrip_without_double_conversion(session):
    """Regression for Patch 01: PARSE_DECLTYPES caused SQLAlchemy's Date
    processor to receive a pre-converted `date` object, raising TypeError
    on read. Assert that a non-null DATE column round-trips cleanly."""
    customer = Customer(name="Test Co")
    assembly = Assembly(part_number="TEST-1")
    session.add_all([customer, assembly])
    session.flush()

    job = Job(
        assembly_id=assembly.id,
        customer_id=customer.id,
        quantity=1,
        status=JobStatus.planned,
        resolved_ship_date=date(2026, 4, 23),
        shipped_at=date(2026, 4, 20),
        kit_released_at=date(2026, 4, 15),
    )
    session.add(job)
    session.flush()
    session.expire_all()

    refetched = session.scalar(select(Job).where(Job.id == job.id))
    assert refetched.resolved_ship_date == date(2026, 4, 23)
    assert refetched.shipped_at         == date(2026, 4, 20)
    assert refetched.kit_released_at    == date(2026, 4, 15)
