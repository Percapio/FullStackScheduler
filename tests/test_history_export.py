"""History CSV export — the 2nd OPS column (Phase 22 2.10).

Decision 16: the column is STATUS ONLY. Grid parity is preserved in structure,
not in cell contents; a CSV cell holding 56 transcribed BOM lines is not
readable.
"""
from __future__ import annotations

from datetime import date, datetime

import pytest
from sqlalchemy import event
from sqlalchemy.orm import Session

from backend.app.models import (
    Assembly,
    BuildType,
    Customer,
    Job,
    JobSecondOpsLine,
    JobStatus,
)
from backend.app.services.history_export import (
    HISTORY_EXPORT_COLUMNS,
    HISTORY_EXPORT_COLUMNS_BY_KEY,
    generate_csv_rows,
)
from backend.app.services.jobs import stream_history_for_export


def _make_shipped_job(session: Session, *, part_number: str, **overrides) -> Job:
    assembly = Assembly(part_number=part_number)
    session.add(assembly)
    session.flush()
    customer = session.scalars(
        __import__("sqlalchemy", fromlist=["select"]).select(Customer).where(
            Customer.name == "ACME"
        )
    ).first()
    if customer is None:
        customer = Customer(name="ACME")
        session.add(customer)
        session.flush()
    defaults = dict(
        assembly_id=assembly.id,
        customer_id=customer.id,
        quantity=10,
        build_type=BuildType.new,
        status=JobStatus.shipped,
        shipped_at=date(2026, 8, 1),
    )
    defaults.update(overrides)
    job = Job(**defaults)
    session.add(job)
    session.flush()
    return job


def _audit(session: Session, job: Job, *, line_count: int, note: str | None = None) -> None:
    for index in range(line_count):
        session.add(
            JobSecondOpsLine(job_id=job.id, line_order=index, find_number=str(index + 1))
        )
    job.second_ops_reviewed_at = datetime(2026, 8, 1, 9, 0)
    job.second_ops_unexpected_inclusions = note
    session.flush()


def _render(session: Session, job: Job) -> str:
    """Render through the real export stream so the undefer is exercised."""
    session.commit()
    session.expire_all()
    column = HISTORY_EXPORT_COLUMNS_BY_KEY["second_ops"]
    streamed = {row.id: row for row in stream_history_for_export(session, None, 500)}
    return column.render(streamed[job.id])


class _QueryCounter:
    def __init__(self, engine):
        self.engine = engine
        self.statements: list[str] = []

    def __enter__(self):
        event.listen(self.engine, "before_cursor_execute", self._record)
        return self

    def __exit__(self, *exc):
        event.remove(self.engine, "before_cursor_execute", self._record)
        return False

    def _record(self, conn, cursor, statement, parameters, context, executemany):
        self.statements.append(statement)

    def __len__(self) -> int:
        return len(self.statements)


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


def test_second_ops_is_registered_last():
    assert [column.key for column in HISTORY_EXPORT_COLUMNS] == [
        "ship_date", "job", "quantity", "build_type", "mfg_notes", "customer",
        "second_ops",
    ]


def test_second_ops_header_text():
    assert HISTORY_EXPORT_COLUMNS_BY_KEY["second_ops"].header == "2nd OPS"


# ---------------------------------------------------------------------------
# Cell contents
# ---------------------------------------------------------------------------


def test_unaudited_renders_empty(session: Session):
    job = _make_shipped_job(session, part_number="EXP-UNAUD")
    assert _render(session, job) == ""


def test_not_applicable_renders_na(session: Session):
    job = _make_shipped_job(session, part_number="EXP-NA")
    _audit(session, job, line_count=0)
    assert _render(session, job) == "N/A"


def test_recorded_renders_audited_with_the_true_count(session: Session):
    job = _make_shipped_job(session, part_number="EXP-REC")
    _audit(session, job, line_count=56)
    assert _render(session, job) == "Audited (56)"


def test_zero_line_job_with_a_note_renders_audited(session: Session):
    job = _make_shipped_job(session, part_number="EXP-NOTE")
    _audit(session, job, line_count=0, note="extra washer in kit")
    assert _render(session, job) == "Audited (0)"


@pytest.mark.parametrize("cell", ["", "N/A", "Audited (56)"])
def test_no_rendered_value_needs_formula_neutralisation(cell):
    assert not cell.startswith(("=", "+", "-", "@"))


# ---------------------------------------------------------------------------
# Query count — asserts the undefer, not a per-row count
# ---------------------------------------------------------------------------


def test_export_query_count_is_independent_of_row_count(session: Session, engine):
    def _export_statement_count(job_total: int) -> int:
        for index in range(job_total):
            job = _make_shipped_job(session, part_number=f"COUNT-{job_total}-{index}")
            _audit(session, job, line_count=3)
        session.commit()
        session.expire_all()
        column = HISTORY_EXPORT_COLUMNS_BY_KEY["second_ops"]
        with _QueryCounter(engine) as counter:
            for row in stream_history_for_export(session, None, 500):
                column.render(row)
        return len(counter)

    two = _export_statement_count(2)
    session.query(Job).delete()
    session.commit()
    ten = _export_statement_count(10)

    assert two == ten


# ---------------------------------------------------------------------------
# CSV assembly
# ---------------------------------------------------------------------------


def test_csv_carries_the_second_ops_header_and_cell(session: Session):
    job = _make_shipped_job(session, part_number="CSV-1")
    _audit(session, job, line_count=4)
    session.commit()
    session.expire_all()

    columns = [HISTORY_EXPORT_COLUMNS_BY_KEY[key] for key in ("job", "second_ops")]
    output = "".join(
        generate_csv_rows(stream_history_for_export(session, None, 500), columns, ",")
    )

    assert "2nd OPS" in output
    assert "Audited (4)" in output


def test_existing_six_columns_render_unchanged(session: Session):
    job = _make_shipped_job(session, part_number="CSV-2")
    session.commit()
    session.expire_all()

    columns = list(HISTORY_EXPORT_COLUMNS[:6])
    output = "".join(
        generate_csv_rows(stream_history_for_export(session, None, 500), columns, ",")
    )

    assert "Ship Date,Job,Qty,ROWC/RONC,Mfg Notes,Customer" in output
    assert "2026-08-01,CSV-2,10,,,ACME" in output


def test_export_endpoint_accepts_the_second_ops_column(client, session):
    job = _make_shipped_job(session, part_number="CSV-API")
    _audit(session, job, line_count=2)
    session.commit()

    response = client.get(
        "/api/jobs/history/export.csv",
        params=[("column", "job"), ("column", "second_ops"), ("delimiter", "comma")],
    )

    assert response.status_code == 200
    assert "Audited (2)" in response.text


def test_export_columns_endpoint_lists_second_ops(client):
    keys = [column["key"] for column in client.get("/api/jobs/history/export-columns").json()]
    assert "second_ops" in keys
