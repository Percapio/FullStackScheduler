"""Regression tests for Stage 5 SAVEPOINT isolation in ingest.py — §7.2 of
20260503-ProjectRefactor02Patch01Implementation01.md.

Covers:
- unparseable SHIPPED during ingest creates no Job
- update branch with bad SHIPPED preserves the pre-existing Job unchanged
- Phase 11 R4 (SO# in raw_job) errors loudly with correct message + highlight
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


# ---------------------------------------------------------------------------
# test_ingest_r4_so_number_in_job_errors_with_correct_fields
# ---------------------------------------------------------------------------

def test_ingest_r4_so_number_in_job_errors_with_correct_fields(
    workbook_factory, session_factory
):
    """Phase 11 Stage 5: a raw_job containing 'SO#' flows through ingest and
    lands on the staging row as an R4 error with the expected message,
    suggestion, and highlight field.

    After ingest:
    - rows_errored == 1, rows_inserted == 0
    - processing_status == error
    - processing_error contains 'SO# is not allowed in JOB cell'
    - suggested_correction contains 'belongs in MFG NOTES'
    - resolve_highlight_fields(processing_error) == ['raw_job']
    """
    from backend.app.errors import resolve_highlight_fields

    path = workbook_factory(
        [{"JOB": "RONC SO#015063", "QTY": "10", "CUSTOMER": "ACME"}],
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
        assert "SO# is not allowed in JOB cell" in row.processing_error
        assert row.suggested_correction is not None
        assert "belongs in MFG NOTES" in row.suggested_correction
        assert resolve_highlight_fields(row.processing_error) == ["raw_job"]
