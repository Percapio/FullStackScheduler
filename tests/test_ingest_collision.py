from sqlalchemy import select

from backend.app.ingest import ingest_workbook
from backend.app.models import ImportStagingRow, ImportStatus, Job


def test_intra_file_duplicates_both_error(workbook_factory, session_factory):
    path = workbook_factory([
        {"JOB": "137845\nNEW", "QTY": "10", "CUSTOMER": "ACME"},
        {"JOB": "137845\nNEW", "QTY": "5", "CUSTOMER": "ACME"},
    ])

    result = ingest_workbook(path, session_factory=session_factory)

    assert result.rows_errored == 2
    assert result.rows_inserted == 0

    with session_factory() as s:
        jobs = s.scalars(select(Job)).all()
        assert len(jobs) == 0

        rows = s.scalars(
            select(ImportStagingRow)
            .where(ImportStagingRow.processing_status == ImportStatus.error)
        ).all()
        assert len(rows) == 2
        for row in rows:
            assert "Intra-file duplicate" in row.processing_error


def test_different_suffix_no_collision(workbook_factory, session_factory):
    path = workbook_factory([
        {"JOB": "137845-1\nNEW", "QTY": "10", "CUSTOMER": "ACME"},
        {"JOB": "137845-2\nNEW", "QTY": "5", "CUSTOMER": "ACME"},
    ])

    result = ingest_workbook(path, session_factory=session_factory)

    assert result.rows_inserted == 2
    assert result.rows_errored == 0


def test_non_colliding_siblings_succeed(workbook_factory, session_factory):
    path = workbook_factory([
        {"JOB": "137845\nNEW", "QTY": "10", "CUSTOMER": "ACME"},
        {"JOB": "137845\nNEW", "QTY": "5", "CUSTOMER": "ACME"},
        {"JOB": "137846\nRONC", "QTY": "3", "CUSTOMER": "Beta"},
    ])

    result = ingest_workbook(path, session_factory=session_factory)

    assert result.rows_errored == 2
    assert result.rows_inserted == 1


def test_intrafile_duplicate_sets_suggested_correction(workbook_factory, session_factory):
    path = workbook_factory([
        {"JOB": "137845\nNEW", "QTY": "10", "CUSTOMER": "ACME"},
        {"JOB": "137845\nNEW", "QTY": "5", "CUSTOMER": "ACME"},
    ])

    ingest_workbook(path, session_factory=session_factory)

    with session_factory() as s:
        rows = s.scalars(
            select(ImportStagingRow)
            .where(ImportStagingRow.processing_status == ImportStatus.error)
        ).all()
        assert len(rows) == 2
        for row in rows:
            assert row.suggested_correction is not None
            assert "137845" in row.suggested_correction
            assert "-1par" in row.suggested_correction
