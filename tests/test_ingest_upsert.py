from sqlalchemy import select

from backend.app.ingest import ingest_workbook
from backend.app.models import Job


def test_re_ingest_updates_in_place(workbook_factory, session_factory):
    path_a = workbook_factory(
        [{"JOB": "137845\nNEW\n(ITAR)", "QTY": "10", "SHIP DATE": "4/17\n15D",
          "CUSTOMER": "ACME", "MFG NOTES": "**warn**", "LINE 2": "x"}],
        filename="a.xlsx",
    )
    first = ingest_workbook(path_a, session_factory=session_factory)
    assert first.rows_inserted == 1 and first.rows_updated == 0

    path_b = workbook_factory(
        [{"JOB": "137845\nNEW\n(ITAR)", "QTY": "12", "SHIP DATE": "5/01\n20D",
          "CUSTOMER": "ACME", "MFG NOTES": "**warn**", "LINE 2": "y"}],
        filename="b.xlsx",
    )
    second = ingest_workbook(path_b, session_factory=session_factory)
    assert second.rows_inserted == 0 and second.rows_updated == 1

    with session_factory() as s:
        jobs = s.scalars(select(Job)).all()
        assert len(jobs) == 1
        assert jobs[0].quantity == 12
        assert jobs[0].ship_lead_time_raw == "20D"
        assert jobs[0].line_2 is True


def test_line_booleans_replaced_on_upsert(workbook_factory, session_factory):
    path_a = workbook_factory(
        [{"JOB": "137845\nNEW", "QTY": "10", "CUSTOMER": "ACME",
          "LINE 1": "x", "LINE 2": "y"}],
        filename="a.xlsx",
    )
    ingest_workbook(path_a, session_factory=session_factory)

    path_b = workbook_factory(
        [{"JOB": "137845\nNEW", "QTY": "10", "CUSTOMER": "ACME",
          "LINE 2": "z"}],
        filename="b.xlsx",
    )
    ingest_workbook(path_b, session_factory=session_factory)

    with session_factory() as s:
        job = s.scalars(select(Job)).one()
        assert job.line_1 is False
        assert job.line_2 is True
        assert job.line_3 is False


def test_repeat_reference_distinguishes_identity(workbook_factory, session_factory):
    path = workbook_factory(
        [
            {"JOB": "137845\nRONC", "QTY": "5", "CUSTOMER": "ACME"},
            {"JOB": "137845\nRONC 123456", "QTY": "8", "CUSTOMER": "ACME"},
        ],
    )
    result = ingest_workbook(path, session_factory=session_factory)
    assert result.rows_inserted == 2

    with session_factory() as s:
        jobs = s.scalars(select(Job).order_by(Job.quantity)).all()
        assert len(jobs) == 2
        assert jobs[0].repeat_reference is None
        assert jobs[0].quantity == 5
        assert jobs[1].repeat_reference == "123456"
        assert jobs[1].quantity == 8
