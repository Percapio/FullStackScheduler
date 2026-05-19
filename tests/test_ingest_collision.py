"""Tests for Stage 4 intra-file duplicate collision behavior.

Phase 18c: Stage 3.6 now catches duplicates and holds the batch for review
before Stage 4 runs. These tests reach Stage 4 directly via run_stages_4_to_6
with the legacy flag True to verify the pre-Phase-18c collision path still works.
"""
from unittest.mock import patch

from sqlalchemy import select

from backend.app.config import Settings
from backend.app.ingest import ingest_workbook, run_stages_4_to_6
from backend.app.models import Assembly, ImportBatch, ImportStagingRow, ImportStatus, Job, SheetKind


def _run_ingest_then_stage4(workbook_factory, session_factory, rows_spec):
    """Helper: ingest a workbook (which holds for review at Stage 3.6), then
    run Stage 4 directly via run_stages_4_to_6 with the legacy collision flag True.

    Returns the IngestResult from run_stages_4_to_6.
    """
    path = workbook_factory(rows_spec)

    # Ingest holds at Stage 3.6 because duplicates are detected.
    held = ingest_workbook(path, session_factory=session_factory)
    assert held.kind == "held_for_review", (
        f"Expected held_for_review but got {held.kind} — test setup incorrect"
    )

    overridden = Settings(intra_file_collision_legacy_error_path=True)
    with patch("backend.app.ingest.get_settings", return_value=overridden):
        return run_stages_4_to_6(
            batch_id=held.batch_id,
            rows_total=len(rows_spec),
            sheet_kind=SheetKind.live,
            source_sha256=held.source_sha256,
            filename=held.filename,
            duplicate_of=None,
            session_factory=session_factory,
        )


def test_intra_file_duplicates_both_error(workbook_factory, session_factory):
    # Phase 18c: with flag True (legacy path), Stage 4 errors both rows.
    result = _run_ingest_then_stage4(workbook_factory, session_factory, [
        {"JOB": "137845\nNEW", "QTY": "10", "CUSTOMER": "ACME"},
        {"JOB": "137845\nNEW", "QTY": "5", "CUSTOMER": "ACME"},
    ])

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
    # Phase 18c: with flag True (legacy path), Stage 4 errors duplicate rows.
    result = _run_ingest_then_stage4(workbook_factory, session_factory, [
        {"JOB": "137845\nNEW", "QTY": "10", "CUSTOMER": "ACME"},
        {"JOB": "137845\nNEW", "QTY": "5", "CUSTOMER": "ACME"},
        {"JOB": "137846\nRONC", "QTY": "3", "CUSTOMER": "Beta"},
    ])

    assert result.rows_errored == 2
    assert result.rows_inserted == 1


def test_intrafile_duplicate_sets_suggested_correction(workbook_factory, session_factory):
    # Phase 18c: with flag True (legacy path), Stage 4 sets suggested_correction.
    result = _run_ingest_then_stage4(workbook_factory, session_factory, [
        {"JOB": "137845\nNEW", "QTY": "10", "CUSTOMER": "ACME"},
        {"JOB": "137845\nNEW", "QTY": "5", "CUSTOMER": "ACME"},
    ])

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
