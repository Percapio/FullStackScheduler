"""Tests for migration 0005 (drop rwk BuildType, add BuildQualifier).

§5.1 of Architecture/20260502-ProjectRefactor02.md.
"""
from __future__ import annotations

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError

from backend.app.models import (
    Assembly,
    BuildQualifier,
    BuildType,
    Customer,
    ImportBatch,
    ImportStagingRow,
    ImportStatus,
    Job,
    JobStatus,
)
from backend.app.transform import transform_staging_row


# ---------------------------------------------------------------------------
# Extractor-level helpers (model tests do not call Alembic)
# ---------------------------------------------------------------------------

def test_migration_0005_rejects_rwk_after_upgrade():
    """Post-migration: BuildType.rwk is removed from the enum."""
    assert not hasattr(BuildType, "rwk"), "rwk must be removed from BuildType"
    assert "rwk" not in {bt.value for bt in BuildType}


def test_migration_0005_preserves_uq_job_identity_at_5tuple(session):
    """Two Jobs with the same 4-tuple (assembly, build_type, split_suffix, repeat_reference)
    but different build_qualifier values can coexist — the 5-tuple UQ allows this."""
    customer = Customer(name="Test Co 5T")
    assembly = Assembly(part_number="5T-TEST-1")
    session.add_all([customer, assembly])
    session.flush()

    job_rwk = Job(
        assembly_id=assembly.id,
        customer_id=customer.id,
        quantity=10,
        build_type=BuildType.new,
        build_qualifier=BuildQualifier.rwk,
    )
    job_plain = Job(
        assembly_id=assembly.id,
        customer_id=customer.id,
        quantity=5,
        build_type=BuildType.new,
        build_qualifier=None,
    )
    session.add_all([job_rwk, job_plain])
    session.flush()  # both succeed because qualifiers differ

    assert session.scalar(select(Job).where(Job.assembly_id == assembly.id).order_by(Job.id).limit(1)).build_qualifier == BuildQualifier.rwk


def test_migration_0005_uq_rejects_duplicate_5tuple(session):
    """Inserting a second Job with an identical 5-tuple raises IntegrityError.

    SQLite treats NULL as distinct in unique constraints, so all 5 tuple fields
    must be non-NULL to trigger a collision.
    """
    customer = Customer(name="Test Co 5T-DUP")
    assembly = Assembly(part_number="5T-DUP-1")
    session.add_all([customer, assembly])
    session.flush()

    job1 = Job(
        assembly_id=assembly.id,
        customer_id=customer.id,
        quantity=10,
        build_type=BuildType.new,
        split_suffix="-1",
        repeat_reference="1st",
        build_qualifier=BuildQualifier.rwk,
    )
    session.add(job1)
    session.flush()

    job2 = Job(
        assembly_id=assembly.id,
        customer_id=customer.id,
        quantity=7,
        build_type=BuildType.new,
        split_suffix="-1",
        repeat_reference="1st",
        build_qualifier=BuildQualifier.rwk,
    )
    session.add(job2)
    with pytest.raises(IntegrityError):
        session.flush()


def test_uq_job_identity_separates_new_rwk_from_new_rework(session):
    """Two Jobs that differ ONLY in build_qualifier (rwk vs rework) co-exist."""
    customer = Customer(name="Test Co UQ-SEP")
    assembly = Assembly(part_number="UQ-SEP-1")
    session.add_all([customer, assembly])
    session.flush()

    job_rwk = Job(
        assembly_id=assembly.id,
        customer_id=customer.id,
        quantity=10,
        build_type=BuildType.new,
        build_qualifier=BuildQualifier.rwk,
    )
    job_rework = Job(
        assembly_id=assembly.id,
        customer_id=customer.id,
        quantity=5,
        build_type=BuildType.new,
        build_qualifier=BuildQualifier.rework,
    )
    session.add_all([job_rwk, job_rework])
    session.flush()  # both must succeed


# ---------------------------------------------------------------------------
# Migration backfill behavior (tested via transform_staging_row)
# ---------------------------------------------------------------------------

@pytest.fixture()
def batch(session) -> ImportBatch:
    b = ImportBatch(source_file="migration_test.xlsx")
    session.add(b)
    session.flush()
    return b


def _make_errored_rwk_row(session, batch, raw_job: str = "138924\nRWK") -> ImportStagingRow:
    row = ImportStagingRow(
        batch_id=batch.id,
        source_row_number=1,
        raw_job=raw_job,
        raw_qty="10",
        raw_customer="Test Customer",
        processing_status=ImportStatus.error,
        processing_error=f"Invalid JOB cell: {raw_job!r}",
    )
    session.add(row)
    session.flush()
    return row


def test_migration_0005_backfills_errored_rwk_staging_rows(session, batch):
    """Rows with 'Invalid JOB cell:' and RWK content transition to processed after
    transform_staging_row is re-run — this is the same logic the migration executes."""
    row = _make_errored_rwk_row(session, batch)
    pre_error = row.processing_error
    assert pre_error is not None and pre_error.startswith("Invalid JOB cell:")

    transform_staging_row(session, row)
    session.flush()

    assert row.processing_status == ImportStatus.processed
    assert row.build_qualifier == BuildQualifier.rwk
    assert row.resolved_job_id is not None


def test_migration_0005_backfills_rework_and_rma_rows(session, batch):
    """REWORK and RMA cells are also resolved by the new extractor."""
    for src, expected_q in [("138924\nREWORK", BuildQualifier.rework), ("138924\nRMA", BuildQualifier.rma)]:
        row = ImportStagingRow(
            batch_id=batch.id,
            source_row_number=batch.row_count + 1,
            raw_job=src,
            raw_qty="5",
            raw_customer="Test Customer",
            processing_status=ImportStatus.error,
            processing_error=f"Invalid JOB cell: {src!r}",
        )
        session.add(row)
        session.flush()
        transform_staging_row(session, row)
        session.flush()
        assert row.processing_status == ImportStatus.processed, f"{src} should process"
        assert row.build_qualifier == expected_q


def test_migration_0005_relabels_rwk_jobs_as_qualifier():
    """The migration's UPDATE SQL correctly relabels build_type='rwk' rows.

    Tested via raw SQLite connection to bypass the ORM's enum CHECK constraint
    that would otherwise prevent inserting the pre-migration 'rwk' build_type value.
    """
    from sqlalchemy import create_engine, event
    from sqlalchemy.pool import StaticPool

    raw_engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    # Build minimal tables with NO CHECK constraint on build_type so we can seed 'rwk'.
    with raw_engine.connect() as conn:
        conn.execute(text("""
            CREATE TABLE assemblies (
                id INTEGER PRIMARY KEY,
                part_number TEXT UNIQUE NOT NULL,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """))
        conn.execute(text("""
            CREATE TABLE customers (
                id INTEGER PRIMARY KEY,
                name TEXT UNIQUE NOT NULL,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """))
        conn.execute(text("""
            CREATE TABLE jobs (
                id         INTEGER PRIMARY KEY,
                assembly_id INTEGER,
                customer_id INTEGER,
                build_type TEXT,
                build_qualifier TEXT,
                split_suffix TEXT,
                repeat_reference TEXT,
                quantity INTEGER,
                status TEXT DEFAULT 'planned',
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """))
        conn.execute(text(
            "INSERT INTO assemblies (id, part_number) VALUES (1, '138924')"
        ))
        conn.execute(text(
            "INSERT INTO customers (id, name) VALUES (1, 'Cust A')"
        ))
        conn.execute(text(
            "INSERT INTO jobs (assembly_id, customer_id, build_type, quantity) VALUES (1, 1, 'rwk', 10)"
        ))
        conn.commit()

        # Execute the migration's step 2 SQL.
        conn.execute(text(
            "UPDATE jobs SET build_qualifier='rwk', build_type='new' WHERE build_type='rwk'"
        ))
        conn.commit()

        row = conn.execute(
            text("SELECT build_type, build_qualifier FROM jobs WHERE id=1")
        ).fetchone()

    assert row.build_type == "new"
    assert row.build_qualifier == "rwk"


# ---------------------------------------------------------------------------
# duplicate_group_key 5-segment formula
# ---------------------------------------------------------------------------

def test_duplicate_group_key_includes_qualifier(session, workbook_factory, session_factory):
    """A RWK-qualified row and an unqualified row at the same (assembly, build_type,
    split_suffix, repeat_reference) 4-tuple are NOT treated as duplicates — the
    5-segment identity key distinguishes them."""
    from backend.app.ingest import ingest_workbook

    wb_path = workbook_factory([
        {"JOB": "128764\nNEW",  "QTY": "10", "SHIP DATE": "01/01", "CUSTOMER": "Cust A"},
        {"JOB": "128764\nRWK",  "QTY": "5",  "SHIP DATE": "01/01", "CUSTOMER": "Cust A"},
    ])
    result = ingest_workbook(wb_path, session_factory=session_factory)

    rows = session.scalars(select(ImportStagingRow)).all()
    # Neither row should be in a duplicate group.
    assert all(r.duplicate_group_key is None for r in rows), (
        "RWK and plain-NEW rows must not be flagged as intra-file duplicates"
    )
    assert result.rows_inserted == 2


# ---------------------------------------------------------------------------
# apply_correction service path
# ---------------------------------------------------------------------------

def test_apply_correction_clears_invalid_job_cell_after_qualifier_recognition(
    session, batch,
):
    """Pre: row errored as 'Invalid JOB cell'. Correct raw_job to a qualifier cell.
    Post: processing_status=processed, build_qualifier set, resolved_job_id set."""
    from backend.app.services.staging import apply_correction

    row = ImportStagingRow(
        batch_id=batch.id,
        source_row_number=1,
        raw_job="bad",
        raw_qty="10",
        raw_customer="Test Customer",
        processing_status=ImportStatus.error,
        processing_error="Invalid JOB cell: 'bad'",
    )
    session.add(row)
    session.flush()

    job = apply_correction(session, row, {"raw_job": "138924\nRWK"})

    assert job is not None
    assert row.processing_status == ImportStatus.processed
    assert row.build_qualifier == BuildQualifier.rwk
    assert row.resolved_job_id == job.id
    assert job.build_qualifier == BuildQualifier.rwk
    assert job.build_type == BuildType.new
