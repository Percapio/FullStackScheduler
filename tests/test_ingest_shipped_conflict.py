from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.ingest import ingest_workbook
from backend.app.models import Assembly, ImportStagingRow, ImportStatus, Job, JobStatus


def test_shipped_job_blocks_update(workbook_factory, session_factory):
    with session_factory() as s:
        from backend.app.models import Customer
        cust = Customer(name="ACME")
        s.add(cust)
        s.flush()
        asm = Assembly(part_number="137845")
        s.add(asm)
        s.flush()
        from backend.app.models import BuildType
        job = Job(
            assembly_id=asm.id,
            customer_id=cust.id,
            build_type=BuildType.new,
            quantity=10,
            status=JobStatus.shipped,
        )
        s.add(job)
        s.commit()
        original_qty = job.quantity

    path = workbook_factory(
        [{"JOB": "137845\nNEW", "QTY": "99", "CUSTOMER": "ACME"}],
    )
    result = ingest_workbook(path, session_factory=session_factory)

    assert result.rows_errored == 1
    assert result.rows_inserted == 0
    assert result.rows_updated == 0

    with session_factory() as s:
        job = s.scalars(select(Job)).one()
        assert job.quantity == original_qty
        assert job.status is JobStatus.shipped

        row = s.scalars(
            select(ImportStagingRow)
            .where(ImportStagingRow.processing_status == ImportStatus.error)
        ).one()
        assert "shipped" in row.processing_error.lower()
        assert row.resolved_job_id is None


def test_shipped_job_conflict_sets_suggested_correction(workbook_factory, session_factory):
    with session_factory() as s:
        from backend.app.models import BuildType, Customer
        cust = Customer(name="ACME")
        s.add(cust)
        s.flush()
        asm = Assembly(part_number="137845")
        s.add(asm)
        s.flush()
        job = Job(
            assembly_id=asm.id,
            customer_id=cust.id,
            build_type=BuildType.new,
            quantity=10,
            status=JobStatus.shipped,
        )
        s.add(job)
        s.commit()

    path = workbook_factory(
        [{"JOB": "137845\nNEW", "QTY": "99", "CUSTOMER": "ACME"}],
    )
    ingest_workbook(path, session_factory=session_factory)

    with session_factory() as s:
        row = s.scalars(
            select(ImportStagingRow)
            .where(ImportStagingRow.processing_status == ImportStatus.error)
        ).one()
        assert row.suggested_correction is not None
        assert "split suffix" in row.suggested_correction.lower()
