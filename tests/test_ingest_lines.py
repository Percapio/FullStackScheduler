from sqlalchemy import select

from backend.app.ingest import ingest_workbook
from backend.app.models import Job


def test_ingest_sets_line_booleans(workbook_factory, session_factory):
    path = workbook_factory(
        [{"JOB": "137845\nNEW", "QTY": "10", "CUSTOMER": "ACME",
          "LINE 1": "SMT-A", "LINE 2": "Wave"}],
    )
    result = ingest_workbook(path, session_factory=session_factory)
    assert result.rows_inserted == 1

    with session_factory() as s:
        job = s.scalars(select(Job)).one()
        assert job.line_1 is True
        assert job.line_2 is True
        assert job.line_3 is False


def test_ingest_empty_line_cells_are_none(workbook_factory, session_factory):
    path = workbook_factory(
        [{"JOB": "137845\nNEW", "QTY": "10", "CUSTOMER": "ACME"}],
    )
    result = ingest_workbook(path, session_factory=session_factory)
    assert result.rows_inserted == 1

    with session_factory() as s:
        job = s.scalars(select(Job)).one()
        assert job.line_1 is False
        assert job.line_2 is False
        assert job.line_3 is False
