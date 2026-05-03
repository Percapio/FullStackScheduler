"""Regression tests for Stage 5 SAVEPOINT isolation in ingest.py — §7.2 of
20260503-ProjectRefactor02Patch01Implementation01.md.

Covers:
- unparseable SHIPPED during ingest creates no Job
- update branch with bad SHIPPED preserves the pre-existing Job unchanged
"""
from __future__ import annotations

from sqlalchemy import func, select

from backend.app.ingest import ingest_workbook
from backend.app.models import (
    Assembly,
    BuildType,
    Customer,
    ImportStagingRow,
    ImportStatus,
    Job,
    JobStatus,
)


# ---------------------------------------------------------------------------
# test_ingest_unparseable_shipped_does_not_create_job
# ---------------------------------------------------------------------------

def test_ingest_unparseable_shipped_does_not_create_job(
    workbook_factory, session_factory
):
    """Ingest one row, valid except SHIPPED is 'banana'.

    After ingest:
    - Job count == 0
    - Row has processing_status = error
    - Row processing_error starts with 'Unparseable SHIPPED'
    """
    path = workbook_factory(
        [{"JOB": "137845\nNEW", "QTY": "10", "CUSTOMER": "ACME",
          "SHIPPED": "banana"}],
    )
    result = ingest_workbook(path, session_factory=session_factory)

    assert result.rows_errored == 1
    assert result.rows_inserted == 0

    with session_factory() as s:
        job_count = s.scalar(select(func.count()).select_from(Job))
        assert job_count == 0

        row = s.scalars(select(ImportStagingRow)).one()
        assert row.processing_status is ImportStatus.error
        assert row.processing_error is not None
        assert row.processing_error.startswith("Unparseable SHIPPED")


# ---------------------------------------------------------------------------
# test_ingest_unparseable_shipped_on_update_branch_preserves_existing_job
# ---------------------------------------------------------------------------

def test_ingest_unparseable_shipped_on_update_branch_preserves_existing_job(
    workbook_factory, session_factory
):
    """Pre-existing Job at the identity with customer X, qty 100.
    Ingest row with same identity, different customer, qty 50, bad SHIPPED.

    After ingest:
    - Existing Job's customer_id and quantity are unchanged
    - Row is errored
    """
    # Seed the pre-existing Job directly
    with session_factory() as s:
        cust = Customer(name="ORIGINAL")
        s.add(cust)
        s.flush()
        asm = Assembly(part_number="137845")
        s.add(asm)
        s.flush()
        job = Job(
            assembly_id=asm.id,
            customer_id=cust.id,
            build_type=BuildType.new,
            quantity=100,
            status=JobStatus.planned,
        )
        s.add(job)
        s.commit()
        original_customer_id = cust.id
        original_qty = job.quantity
        job_id = job.id

    path = workbook_factory(
        [{"JOB": "137845\nNEW", "QTY": "50", "CUSTOMER": "NEWCUST",
          "SHIPPED": "not-a-date"}],
    )
    result = ingest_workbook(path, session_factory=session_factory)

    assert result.rows_errored == 1
    assert result.rows_updated == 0
    assert result.rows_inserted == 0

    with session_factory() as s:
        job = s.get(Job, job_id)
        assert job.quantity == original_qty, "quantity must not be mutated by errored ingest"
        assert job.customer_id == original_customer_id, (
            "customer_id must not be mutated by errored ingest"
        )

        row = s.scalars(select(ImportStagingRow)).one()
        assert row.processing_status is ImportStatus.error
        assert row.resolved_job_id is None
