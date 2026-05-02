from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from backend.app.models import (
    Assembly,
    Base,
    BuildType,
    Customer,
    ImportBatch,
    ImportStagingRow,
    ImportStatus,
    Job,
    JobStatus,
    Salesperson,
)


@pytest.fixture()
def engine():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(engine, "connect")
    def _enable_foreign_keys(dbapi_connection, _connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    Base.metadata.create_all(engine)
    try:
        yield engine
    finally:
        engine.dispose()


@pytest.fixture()
def session(engine) -> Session:
    TestSession = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    with TestSession() as s:
        yield s


@pytest.fixture()
def session_factory(engine):
    def _factory() -> Session:
        return sessionmaker(
            bind=engine, autoflush=False, expire_on_commit=False,
        )()
    return _factory


@pytest.fixture()
def open_batch(session: Session) -> ImportBatch:
    batch = ImportBatch(source_file="test.xlsx")
    session.add(batch)
    session.flush()
    return batch


@pytest.fixture()
def workbook_factory(tmp_path):
    from backend.app.reader import KNOWN_HEADERS

    _headers = sorted(KNOWN_HEADERS)

    def _create(
        rows: list[dict[str, str | None]],
        filename: str = "test.xlsx",
    ) -> Path:
        from openpyxl import Workbook
        wb = Workbook()
        ws = wb.active
        ws.title = "SHIPPED (AA)"
        ws.append(_headers)
        for row_data in rows:
            ws.append([row_data.get(h) for h in _headers])
        path = tmp_path / filename
        wb.save(path)
        return path

    return _create


# ---- FastAPI test client ------------------------------------------------------


@pytest.fixture()
def client(session_factory):
    from backend.app.api import create_app
    from backend.app.api.deps import get_session

    app = create_app()

    def _override_get_session():
        s = session_factory()
        try:
            yield s
        finally:
            s.close()

    app.dependency_overrides[get_session] = _override_get_session
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


# ---- seeded fixtures (staging) -----------------------------------------------


def _make_errored_row(session, batch, **overrides):
    defaults = dict(
        batch_id=batch.id,
        source_row_number=1,
        processing_status=ImportStatus.error,
        processing_error="test error",
    )
    defaults.update(overrides)
    row = ImportStagingRow(**defaults)
    session.add(row)
    session.flush()
    return row


@pytest.fixture()
def seeded_mixed_staging(session, open_batch):
    rows = []
    for i, st in enumerate(
        [ImportStatus.pending, ImportStatus.error, ImportStatus.error,
         ImportStatus.processed, ImportStatus.error],
        start=1,
    ):
        row = ImportStagingRow(
            batch_id=open_batch.id,
            source_row_number=i,
            processing_status=st,
            processing_error="err" if st == ImportStatus.error else None,
        )
        session.add(row)
        rows.append(row)
    session.flush()
    return {"errored_count": 3, "rows": rows}


@pytest.fixture()
def seeded_25_errored(session, open_batch):
    for i in range(1, 26):
        session.add(ImportStagingRow(
            batch_id=open_batch.id,
            source_row_number=i,
            processing_status=ImportStatus.error,
            processing_error=f"error {i}",
        ))
    session.flush()


@pytest.fixture()
def seeded_errored_row(session, open_batch):
    return _make_errored_row(
        session, open_batch,
        raw_job="bad-job-cell",
        raw_prog="PROG-A",
        processing_error="Invalid JOB cell: 'bad-job-cell'",
    )


@pytest.fixture()
def seeded_errored_row_invalid_job(session, open_batch):
    return _make_errored_row(
        session, open_batch,
        raw_job="not-a-job-cell",
        processing_error="Invalid JOB cell: 'not-a-job-cell'",
    )


@pytest.fixture()
def seeded_processed_row(session, open_batch):
    customer = Customer(name="ProcessedCo")
    session.add(customer)
    session.flush()
    assembly = Assembly(part_number="PROC-001")
    session.add(assembly)
    session.flush()
    job = Job(
        assembly_id=assembly.id, customer_id=customer.id,
        quantity=1, build_type=BuildType.new,
    )
    session.add(job)
    session.flush()
    row = ImportStagingRow(
        batch_id=open_batch.id,
        source_row_number=1,
        processing_status=ImportStatus.processed,
        resolved_job_id=job.id,
    )
    session.add(row)
    session.flush()
    return row


# ---- seeded fixtures (jobs) --------------------------------------------------


def _make_job(session, *, part_number="TEST-001", customer_name="TestCo", **overrides):
    assembly = Assembly(part_number=part_number)
    session.add(assembly)
    session.flush()
    customer = session.execute(
        select(Customer).where(Customer.name == customer_name)
    ).scalar_one_or_none()
    if customer is None:
        customer = Customer(name=customer_name)
        session.add(customer)
        session.flush()
    defaults = dict(
        assembly_id=assembly.id,
        customer_id=customer.id,
        quantity=10,
        build_type=BuildType.new,
    )
    defaults.update(overrides)
    job = Job(**defaults)
    session.add(job)
    session.flush()
    return job


@pytest.fixture()
def seeded_jobs(session):
    jobs = []
    for i in range(3):
        jobs.append(_make_job(
            session,
            part_number=f"JOB-{i:03d}",
            customer_name=f"Customer-{i}",
        ))
    return jobs


@pytest.fixture()
def seeded_mixed_status_jobs(session):
    jobs = []
    for i, st in enumerate([JobStatus.planned, JobStatus.wip, JobStatus.shipped]):
        jobs.append(_make_job(
            session,
            part_number=f"STATUS-{i:03d}",
            status=st,
        ))
    return jobs


@pytest.fixture()
def seeded_split_jobs(session):
    customer = Customer(name="SplitCo")
    session.add(customer)
    session.flush()
    assembly = Assembly(part_number="SPLIT-001")
    session.add(assembly)
    session.flush()
    suffixes = [None, "-1par", "-2bal"]
    for sfx in suffixes:
        session.add(Job(
            assembly_id=assembly.id,
            customer_id=customer.id,
            quantity=5,
            build_type=BuildType.new,
            split_suffix=sfx,
        ))
    session.flush()
    return {"assembly_id": assembly.id}


@pytest.fixture()
def seeded_split_jobs_mixed_status(session):
    customer = Customer(name="SplitMixCo")
    session.add(customer)
    session.flush()
    assembly = Assembly(part_number="SPLITMIX-001")
    session.add(assembly)
    session.flush()
    for sfx, st in [
        (None, JobStatus.planned),
        ("-1par", JobStatus.wip),
        ("-2bal", JobStatus.planned),
    ]:
        session.add(Job(
            assembly_id=assembly.id,
            customer_id=customer.id,
            quantity=5,
            build_type=BuildType.new,
            split_suffix=sfx,
            status=st,
        ))
    session.flush()
    return {"assembly_id": assembly.id}


@pytest.fixture()
def seeded_mixed_build_jobs(session):
    jobs = []
    for i, bt in enumerate([BuildType.new, BuildType.ronc, BuildType.rowc]):
        jobs.append(_make_job(
            session,
            part_number=f"BUILD-{i:03d}",
            build_type=bt,
        ))
    return jobs


@pytest.fixture()
def seeded_50_jobs(session):
    customer = Customer(name="BulkCo")
    session.add(customer)
    session.flush()
    for i in range(50):
        assembly = Assembly(part_number=f"BULK-{i:03d}")
        session.add(assembly)
        session.flush()
        session.add(Job(
            assembly_id=assembly.id,
            customer_id=customer.id,
            quantity=1,
            build_type=BuildType.new,
        ))
    session.flush()


@pytest.fixture()
def seeded_jobs_with_ship_dates(session):
    customer = Customer(name="DateCo")
    session.add(customer)
    session.flush()
    dates = [
        date(2026, 3, 15), None, date(2026, 1, 15),
        date(2026, 4, 15), date(2026, 2, 15), None,
    ]
    for i, d in enumerate(dates):
        assembly = Assembly(part_number=f"DATE-{i:03d}")
        session.add(assembly)
        session.flush()
        session.add(Job(
            assembly_id=assembly.id,
            customer_id=customer.id,
            quantity=1,
            build_type=BuildType.new,
            shipped_at=d,
        ))
    session.flush()


@pytest.fixture()
def seeded_job_markdown(session):
    return _make_job(
        session,
        part_number="MD-001",
        run_mfg_notes="**bold**\n\n- list",
    )


@pytest.fixture()
def seeded_assemblies(session):
    assemblies = []
    for i in range(3):
        a = Assembly(part_number=f"ASM-{i:03d}")
        session.add(a)
        assemblies.append(a)
    session.flush()
    return assemblies


@pytest.fixture()
def seeded_job(session):
    return _make_job(session, part_number="DETAIL-001", customer_name="DetailCo")
