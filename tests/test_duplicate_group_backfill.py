"""Tests for ingest Stage 4 duplicate_group_key population and migration backfill.

§5 of Architecture/20260502-ProjectRefactor01c.md.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest
from sqlalchemy import select, text

from backend.app.models import ImportBatch, ImportStagingRow, ImportStatus
from backend.app.services.staging import _DUPLICATE_ERROR_PREFIX


def _load_migration():
    """Load 0004_duplicate_group_key.py by path (name starts with digit)."""
    migration_path = (
        Path(__file__).parent.parent
        / "backend" / "alembic" / "versions" / "0004_duplicate_group_key.py"
    )
    spec = importlib.util.spec_from_file_location("migration_0004", migration_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------------------
# Ingest writes duplicate_group_key for duplicated rows
# ---------------------------------------------------------------------------

def test_ingest_sets_duplicate_group_key_for_dup_rows(session, open_batch, workbook_factory, session_factory):
    """Stage 4 of ingest must write duplicate_group_key on rows whose JOB identity
    appears more than once in the batch."""
    from backend.app.ingest import ingest_workbook

    wb_path = workbook_factory(
        [
            {"JOB": "128764\nNEW", "QTY": "10", "SHIP DATE": "01/01", "CUSTOMER": "Cust A"},
            {"JOB": "128764\nNEW", "QTY": "5",  "SHIP DATE": "01/01", "CUSTOMER": "Cust B"},
            {"JOB": "99999\nNEW",  "QTY": "3",  "SHIP DATE": "01/01", "CUSTOMER": "Cust C"},
        ]
    )
    result = ingest_workbook(wb_path, session_factory=session_factory)
    session.expire_all()

    dup_rows = session.scalars(
        select(ImportStagingRow).where(
            ImportStagingRow.duplicate_group_key.is_not(None),
        )
    ).all()
    unique_rows = session.scalars(
        select(ImportStagingRow).where(
            ImportStagingRow.duplicate_group_key.is_(None),
            ImportStagingRow.processing_status == ImportStatus.error,
        )
    ).all()

    assert len(dup_rows) == 2, "both duplicate rows should have a group key"
    assert len(unique_rows) == 0 or all(
        r.processing_error is None or _DUPLICATE_ERROR_PREFIX not in r.processing_error
        for r in unique_rows
    ), "unique rows must not have a duplicate-error message"
    # Both dup rows share the same key.
    assert dup_rows[0].duplicate_group_key == dup_rows[1].duplicate_group_key


def test_ingest_unique_row_has_null_group_key(session, open_batch, workbook_factory, session_factory):
    """A row whose JOB identity is unique in the batch must have duplicate_group_key IS NULL."""
    from backend.app.ingest import ingest_workbook

    wb_path = workbook_factory(
        [{"JOB": "99999\nNEW", "QTY": "3", "SHIP DATE": "01/01", "CUSTOMER": "Cust C"}]
    )
    ingest_workbook(wb_path, session_factory=session_factory)
    session.expire_all()

    rows = session.scalars(
        select(ImportStagingRow)
    ).all()
    for r in rows:
        assert r.duplicate_group_key is None


def test_ingest_group_key_matches_processing_error_prefix(session, open_batch, workbook_factory, session_factory):
    """The duplicate_group_key value must embed in the processing_error message,
    confirming the format is byte-for-byte consistent between the column and the message."""
    from backend.app.ingest import ingest_workbook

    wb_path = workbook_factory(
        [
            {"JOB": "128764\nNEW", "QTY": "10", "SHIP DATE": "01/01", "CUSTOMER": "Cust"},
            {"JOB": "128764\nNEW", "QTY": "5",  "SHIP DATE": "01/01", "CUSTOMER": "Cust"},
        ]
    )
    ingest_workbook(wb_path, session_factory=session_factory)
    session.expire_all()

    dup_rows = session.scalars(
        select(ImportStagingRow).where(
            ImportStagingRow.duplicate_group_key.is_not(None),
        )
    ).all()
    for r in dup_rows:
        assert r.duplicate_group_key is not None
        assert r.duplicate_group_key in r.processing_error, (
            f"group key {r.duplicate_group_key!r} not found in processing_error {r.processing_error!r}"
        )


# ---------------------------------------------------------------------------
# Migration backfill populates duplicate_group_key from processing_error
# ---------------------------------------------------------------------------

def test_migration_backfill_populates_group_key(session, open_batch):
    """_backfill in migration 0004 should parse the processing_error and set
    duplicate_group_key from the raw_job via decompose_job_string."""
    migration = _load_migration()

    # Simulate pre-Phase-3 rows: errored with duplicate prefix, key IS NULL.
    row_a = ImportStagingRow(
        batch_id=open_batch.id,
        source_row_number=1,
        raw_job="128764\nNEW",
        processing_status=ImportStatus.error,
        processing_error=f"{_DUPLICATE_ERROR_PREFIX} 128764|new|| (staging rows [1, 2])",
        duplicate_group_key=None,
    )
    row_b = ImportStagingRow(
        batch_id=open_batch.id,
        source_row_number=2,
        raw_job="128764\nNEW",
        processing_status=ImportStatus.error,
        processing_error=f"{_DUPLICATE_ERROR_PREFIX} 128764|new|| (staging rows [1, 2])",
        duplicate_group_key=None,
    )
    session.add_all([row_a, row_b])
    session.commit()

    # Run the backfill against the underlying connection.
    with session.get_bind().connect() as conn:
        migration._backfill(conn)
        conn.commit()

    session.expire_all()
    session.refresh(row_a)
    session.refresh(row_b)

    assert row_a.duplicate_group_key == "128764|new||"
    assert row_b.duplicate_group_key == "128764|new||"


def test_migration_backfill_skips_discarded_rows(session, open_batch):
    """The backfill must not populate duplicate_group_key for discarded rows."""
    from datetime import UTC, datetime

    migration = _load_migration()
    row = ImportStagingRow(
        batch_id=open_batch.id,
        source_row_number=1,
        raw_job="128764\nNEW",
        processing_status=ImportStatus.error,
        processing_error=f"{_DUPLICATE_ERROR_PREFIX} 128764|new|| (staging rows [1, 2])",
        duplicate_group_key=None,
        discarded_at=datetime.now(UTC),
    )
    session.add(row)
    session.commit()

    with session.get_bind().connect() as conn:
        migration._backfill(conn)
        conn.commit()

    session.expire_all()
    session.refresh(row)
    assert row.duplicate_group_key is None


def test_migration_backfill_skips_unparseable_raw_job(session, open_batch):
    """Rows whose raw_job does not parse stay NULL after the backfill."""
    migration = _load_migration()

    row = ImportStagingRow(
        batch_id=open_batch.id,
        source_row_number=1,
        raw_job=None,  # unparseable
        processing_status=ImportStatus.error,
        processing_error=f"{_DUPLICATE_ERROR_PREFIX} 128764|new|| (staging rows [1, 2])",
        duplicate_group_key=None,
    )
    session.add(row)
    session.commit()

    with session.get_bind().connect() as conn:
        migration._backfill(conn)
        conn.commit()

    session.expire_all()
    session.refresh(row)
    assert row.duplicate_group_key is None
