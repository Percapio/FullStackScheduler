from datetime import date

from sqlalchemy import select

from backend.app.ingest import ingest_workbook
from backend.app.models import Job, JobStatus


def test_shipped_with_full_date_creates_shipped_job(workbook_factory, session_factory):
    path = workbook_factory(
        [{"JOB": "137845\nNEW", "QTY": "10", "CUSTOMER": "ACME",
          "SHIPPED": "04/14/2026"}],
    )
    result = ingest_workbook(path, session_factory=session_factory)

    assert result.rows_inserted == 1

    with session_factory() as s:
        job = s.scalars(select(Job)).one()
        assert job.status is JobStatus.shipped
        assert job.shipped_at == date(2026, 4, 14)


def test_shipped_yearless_errors_row(workbook_factory, session_factory):
    path = workbook_factory(
        [{"JOB": "137845\nNEW", "QTY": "10", "CUSTOMER": "ACME",
          "SHIPPED": "4/14"}],
    )
    result = ingest_workbook(path, session_factory=session_factory)

    assert result.rows_errored == 1
    assert result.rows_inserted == 0


def test_kit_released_at_parses_full_date(workbook_factory, session_factory):
    path = workbook_factory(
        [{"JOB": "137845\nNEW", "QTY": "10", "CUSTOMER": "ACME",
          "KIT REL": "04/15/2026"}],
    )
    result = ingest_workbook(path, session_factory=session_factory)

    assert result.rows_inserted == 1

    with session_factory() as s:
        job = s.scalars(select(Job)).one()
        assert job.kit_released_at == date(2026, 4, 15)
