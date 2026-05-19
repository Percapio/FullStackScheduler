"""Regression tests for apply_correction's SAVEPOINT isolation — §7.2 of
20260503-ProjectRefactor02Patch01Implementation01.md.

Covers:
- errored correction does not mutate Job (F1 fix)
- successive errors update error fields (not preserved as history)
- previously-resolved job is unmutated when correction errors
- sibling-collision branch stamps explicit duplicate error markers
"""
from __future__ import annotations

from sqlalchemy import func, select

from unittest.mock import patch

from backend.app.config import Settings
from backend.app.ingest import ingest_workbook, run_stages_4_to_6
from backend.app.models import (
    Assembly,
    BuildType,
    Customer,
    ImportBatch,
    ImportStagingRow,
    ImportStatus,
    Job,
    JobStatus,
    SheetKind,
)
from backend.app.services.staging import (
    _DUPLICATE_ERROR_PREFIX,
    _duplicate_collision_error_message,
    apply_correction,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _seed_errored_row(session, *, raw_job="137845\nNEW", raw_qty="10",
                      raw_customer="ACME", raw_shipped="bad-date",
                      processing_error="prior error"):
    """Insert an ImportBatch + ImportStagingRow already in errored state."""
    batch = ImportBatch(source_file="test.xlsx")
    session.add(batch)
    session.flush()
    row = ImportStagingRow(
        batch_id=batch.id,
        source_row_number=1,
        raw_job=raw_job,
        raw_qty=raw_qty,
        raw_customer=raw_customer,
        raw_shipped=raw_shipped,
        processing_status=ImportStatus.error,
        processing_error=processing_error,
    )
    session.add(row)
    session.commit()
    return row


# ---------------------------------------------------------------------------
# test_apply_correction_unparseable_shipped_does_not_persist_job
# ---------------------------------------------------------------------------

def test_apply_correction_unparseable_shipped_does_not_persist_job(session):
    """Corrected row with bad SHIPPED: apply_correction returns None, no Job created."""
    row = _seed_errored_row(session, raw_shipped=None, processing_error="original error")

    result = apply_correction(session, row, {"raw_shipped": "not-a-date"})

    assert result is None
    assert session.scalar(select(func.count()).select_from(Job)) == 0
    session.expire(row)
    row = session.get(ImportStagingRow, row.id)
    assert row.processing_status is ImportStatus.error
    assert "Unparseable SHIPPED" in row.processing_error


# ---------------------------------------------------------------------------
# test_apply_correction_successive_errors_update_error_fields
# ---------------------------------------------------------------------------

def test_apply_correction_successive_errors_update_error_fields(session):
    """Three successive erroring corrections — each replaces the prior error message."""
    row = _seed_errored_row(session, raw_shipped=None)

    apply_correction(session, row, {"raw_shipped": "first-bad"})
    session.expire(row)
    row = session.get(ImportStagingRow, row.id)
    first_error = row.processing_error
    assert "first-bad" in first_error or "Unparseable" in first_error

    apply_correction(session, row, {"raw_shipped": "second-bad"})
    session.expire(row)
    row = session.get(ImportStagingRow, row.id)
    second_error = row.processing_error
    assert second_error != "prior error", "Prior error must not survive"
    assert "second-bad" in second_error or "Unparseable" in second_error

    apply_correction(session, row, {"raw_shipped": "third-bad"})
    session.expire(row)
    row = session.get(ImportStagingRow, row.id)
    third_error = row.processing_error
    assert third_error != second_error or "third-bad" in third_error or "Unparseable" in third_error
    # Most important: no Job was ever created
    assert session.scalar(select(func.count()).select_from(Job)) == 0


# ---------------------------------------------------------------------------
# test_apply_correction_with_pre_existing_resolved_job_id_then_error_leaves_prior_job_intact
# ---------------------------------------------------------------------------

def test_apply_correction_with_pre_existing_job_then_error_leaves_prior_job_intact(session):
    """When a previously-resolved Job exists and correction errors, the Job is unmutated."""
    # Seed Job directly at the target identity
    cust = Customer(name="ACME")
    session.add(cust)
    session.flush()
    asm = Assembly(part_number="137845")
    session.add(asm)
    session.flush()
    job = Job(
        assembly_id=asm.id,
        customer_id=cust.id,
        build_type=BuildType.new,
        quantity=100,
        status=JobStatus.planned,
    )
    session.add(job)
    session.flush()

    batch = ImportBatch(source_file="test.xlsx")
    session.add(batch)
    session.flush()
    row = ImportStagingRow(
        batch_id=batch.id,
        source_row_number=1,
        raw_job="137845\nNEW",
        raw_qty="100",
        raw_customer="ACME",
        processing_status=ImportStatus.error,
        processing_error="prior error",
        resolved_job_id=job.id,
    )
    session.add(row)
    session.commit()

    original_qty = job.quantity

    result = apply_correction(session, row, {"raw_shipped": "bad-date", "raw_qty": "999"})

    assert result is None
    session.expire(job)
    job = session.get(Job, job.id)
    assert job.quantity == original_qty, "Job quantity must not be mutated by an errored correction"



# ---------------------------------------------------------------------------
# test_apply_correction_sibling_collision_stamps_duplicate_error
# ---------------------------------------------------------------------------

def test_apply_correction_sibling_collision_stamps_duplicate_error(
    workbook_factory, session_factory
):
    """When a corrected row's identity still has an active sibling,
    apply_correction rolls back and returns None.

    Two duplicate rows at the same identity.  Applying an empty-payload
    correction to row[0] (identity unchanged) finds row[1] as an active
    sibling and triggers the collision path.
    """
    path = workbook_factory(
        [
            {"JOB": "137845\nNEW", "QTY": "10", "CUSTOMER": "ACME"},
            {"JOB": "137845\nNEW", "QTY": "20", "CUSTOMER": "BETA"},
        ],
    )
    # Phase 18c: Stage 3.6 holds the batch; run Stage 4 directly with flag True.
    held = ingest_workbook(path, session_factory=session_factory)
    overridden = Settings(intra_file_collision_legacy_error_path=True)
    with patch("backend.app.ingest.get_settings", return_value=overridden):
        run_stages_4_to_6(
            batch_id=held.batch_id,
            rows_total=2,
            sheet_kind=SheetKind.live,
            source_sha256=held.source_sha256,
            filename=held.filename,
            duplicate_of=None,
            session_factory=session_factory,
        )

    with session_factory() as s:
        rows = s.scalars(
            select(ImportStagingRow)
            .where(ImportStagingRow.processing_status == ImportStatus.error)
            .order_by(ImportStagingRow.id)
        ).all()
        assert len(rows) == 2, "Both rows should be errored as duplicates"

        # Empty payload: identity unchanged, so row[1] is still an active sibling.
        result = apply_correction(s, rows[0], {})

    assert result is None


def test_apply_correction_sibling_collision_stamps_error_fields(
    workbook_factory, session_factory
):
    """Sibling collision: explicit processing_error, suggested_correction,
    and duplicate_group_key are stamped; resolved_job_id stays NULL.
    """
    path = workbook_factory(
        [
            {"JOB": "137845\nNEW", "QTY": "10", "CUSTOMER": "ACME"},
            {"JOB": "137845\nNEW", "QTY": "20", "CUSTOMER": "BETA"},
        ],
    )
    # Phase 18c: Stage 3.6 holds the batch; run Stage 4 directly with flag True.
    held = ingest_workbook(path, session_factory=session_factory)
    overridden = Settings(intra_file_collision_legacy_error_path=True)
    with patch("backend.app.ingest.get_settings", return_value=overridden):
        run_stages_4_to_6(
            batch_id=held.batch_id,
            rows_total=2,
            sheet_kind=SheetKind.live,
            source_sha256=held.source_sha256,
            filename=held.filename,
            duplicate_of=None,
            session_factory=session_factory,
        )

    with session_factory() as s:
        row = s.scalars(
            select(ImportStagingRow)
            .where(ImportStagingRow.processing_status == ImportStatus.error)
            .order_by(ImportStagingRow.id)
        ).first()
        row_id = row.id
        apply_correction(s, row, {})

    with session_factory() as s:
        row = s.get(ImportStagingRow, row_id)
        assert row.processing_status is ImportStatus.error
        assert row.processing_error is not None, "processing_error must be set on collision"
        assert row.suggested_correction is not None, "suggested_correction must be set on collision"
        assert row.duplicate_group_key is not None
        assert row.resolved_job_id is None
