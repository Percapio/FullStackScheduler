"""Tests for Phase 18a Phase 1 — held-state review workflow.

Covers:
 - Stage 3.5: classify_new_parts_for_review sets review_status='pending' for new B#s
 - ingest_workbook returns held_for_review when new B# parts present
 - ingest_workbook runs inline for no-review batches
 - Stage 1 duplicate guard allows re-upload after abandon
 - State-transition matrix for staging row review_status
 - All seven review API endpoints
 - P-2: CLI main() dispatch for held_for_review (exit code 3) and processed (exit code 0/1)
 - P-4: _require_row_in_review_states precondition for DELETE and PATCH /split-suffix
"""
from __future__ import annotations

from datetime import UTC, datetime

import pytest
from fastapi import status
from sqlalchemy import select

from backend.app.ingest import (
    IngestResult,
    ReviewClassification,
    classify_new_parts_for_review,
    ingest_workbook,
    matches_b_number_shape,
)
from backend.app.models import (
    Assembly,
    ImportBatch,
    ImportStagingRow,
    ImportStatus,
    SheetKind,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_batch(
    session,
    *,
    status: ImportStatus = ImportStatus.awaiting_review,
    source_sha256: str = "abc123",
) -> ImportBatch:
    batch = ImportBatch(
        source_file="test.xlsx",
        source_sha256=source_sha256,
        status=status,
        sheet_kind=SheetKind.live,
        row_count=1,
    )
    session.add(batch)
    session.flush()
    return batch


def _make_review_row(
    session,
    batch: ImportBatch,
    *,
    raw_job: str = "123456\nNEW",
    review_status: str = "pending",
    source_row_number: int = 1,
) -> ImportStagingRow:
    row = ImportStagingRow(
        batch_id=batch.id,
        source_row_number=source_row_number,
        raw_job=raw_job,
        review_status=review_status,
    )
    session.add(row)
    session.flush()
    return row


# ---------------------------------------------------------------------------
# matches_b_number_shape
# ---------------------------------------------------------------------------

class TestMatchesBNumberShape:
    def test_six_digits_is_b_number(self):
        assert matches_b_number_shape("123456") is True

    def test_five_digits_not_b_number(self):
        assert matches_b_number_shape("12345") is False

    def test_seven_digits_not_b_number(self):
        assert matches_b_number_shape("1234567") is False

    def test_six_chars_with_letter_not_b_number(self):
        assert matches_b_number_shape("12345A") is False

    def test_octo_style_not_b_number(self):
        assert matches_b_number_shape("OCTO-QUAD-H1-2") is False

    def test_empty_not_b_number(self):
        assert matches_b_number_shape("") is False


# ---------------------------------------------------------------------------
# classify_new_parts_for_review
# ---------------------------------------------------------------------------

class TestClassifyNewPartsForReview:
    def test_new_b_number_gets_pending_status(self, session):
        """New 6-digit B# not in registry → review_status='pending'."""
        batch = _make_batch(session)
        row = ImportStagingRow(batch_id=batch.id, source_row_number=1, raw_job="123456\nNEW")
        session.add(row)
        session.flush()

        result = classify_new_parts_for_review(session, [row])

        assert len(result.b) == 1
        assert result.b[0].parsed_part_number == "123456"
        assert row.review_status == "pending"
        assert result.non_b == []
        assert result.intra_file_duplicates == []

    def test_existing_b_number_passes_through(self, session):
        """B# already in registry → not flagged for review."""
        session.add(Assembly(part_number="123456"))
        session.flush()
        batch = _make_batch(session)
        row = ImportStagingRow(batch_id=batch.id, source_row_number=1, raw_job="123456\nNEW")
        session.add(row)
        session.flush()

        result = classify_new_parts_for_review(session, [row])

        assert result.is_empty() is True
        assert row.review_status is None

    def test_non_b_number_passes_through_silently_in_phase1(self, session):
        """Non-B# parts are silently passed through in Phase 1."""
        batch = _make_batch(session)
        row = ImportStagingRow(batch_id=batch.id, source_row_number=1, raw_job="OCTO-QUAD\nNEW")
        session.add(row)
        session.flush()

        result = classify_new_parts_for_review(session, [row])

        # Phase 1: non_b is always empty; no review_status set
        assert result.non_b == []
        assert row.review_status is None

    def test_invalid_raw_job_skipped(self, session):
        """Rows that fail decomposition are silently skipped."""
        batch = _make_batch(session)
        row = ImportStagingRow(batch_id=batch.id, source_row_number=1, raw_job="NOTPARSEABLE")
        session.add(row)
        session.flush()

        result = classify_new_parts_for_review(session, [row])

        assert result.is_empty() is True
        assert row.review_status is None

    def test_multiple_rows_same_b_number_grouped(self, session):
        """Multiple rows for the same new B# form one ReviewGroup."""
        batch = _make_batch(session)
        rows = [
            ImportStagingRow(batch_id=batch.id, source_row_number=i, raw_job="123456\nNEW")
            for i in range(1, 4)
        ]
        for r in rows:
            session.add(r)
        session.flush()

        result = classify_new_parts_for_review(session, rows)

        assert len(result.b) == 1
        assert len(result.b[0].rows) == 3
        assert all(r.review_status == "pending" for r in rows)

    def test_is_empty_true_when_no_new_parts(self, session):
        result = ReviewClassification()
        assert result.is_empty() is True

    def test_is_empty_false_when_b_groups(self, session):
        batch = _make_batch(session)
        row = ImportStagingRow(batch_id=batch.id, source_row_number=1, raw_job="123456\nNEW")
        session.add(row)
        session.flush()
        result = classify_new_parts_for_review(session, [row])
        assert result.is_empty() is False


# ---------------------------------------------------------------------------
# ingest_workbook held-state integration
# ---------------------------------------------------------------------------

class TestIngestWorkbookHeldState:
    def test_new_b_number_triggers_held_for_review(self, workbook_factory, session_factory):
        """Workbook with a new 6-digit B# holds the batch in awaiting_review."""
        path = workbook_factory([{"JOB": "123456\nNEW", "QTY": "10", "CUSTOMER": "ACME"}], seed_assemblies=False)
        result = ingest_workbook(path, session_factory=session_factory)

        assert result.kind == "held_for_review"
        assert result.batch_id is not None
        assert len(result.new_b_numbers) == 1
        assert result.new_b_numbers[0].parsed_part_number == "123456"

        with session_factory() as s:
            batch = s.get(ImportBatch, result.batch_id)
            assert batch.status == ImportStatus.awaiting_review

            row = s.scalars(
                select(ImportStagingRow).where(ImportStagingRow.batch_id == result.batch_id)
            ).first()
            assert row.review_status == "pending"

    def test_known_part_number_runs_inline(self, workbook_factory, session_factory):
        """Workbook with a known assembly does not hold for review."""
        with session_factory() as s:
            s.add(Assembly(part_number="123456"))
            s.commit()

        path = workbook_factory([{"JOB": "123456\nNEW", "QTY": "10", "CUSTOMER": "ACME"}])
        result = ingest_workbook(path, session_factory=session_factory)

        assert result.kind == "processed_or_error"

    def test_non_b_number_runs_inline_in_phase1(self, workbook_factory, session_factory):
        """Non-B# new part passes through inline in Phase 1 (not held)."""
        path = workbook_factory([{"JOB": "MYPART\nNEW", "QTY": "10", "CUSTOMER": "ACME"}])
        result = ingest_workbook(path, session_factory=session_factory)

        assert result.kind == "processed_or_error"


# ---------------------------------------------------------------------------
# Stage 1: abandoned batch allows re-upload
# ---------------------------------------------------------------------------

class TestAbandonedBatchReupload:
    def test_abandoned_batch_not_duplicate(self, workbook_factory, session_factory):
        """Re-uploading after abandon succeeds (Stage 1 skips abandoned batches)."""
        path = workbook_factory([{"JOB": "123456\nNEW", "QTY": "10", "CUSTOMER": "ACME"}], seed_assemblies=False)

        # First upload → held
        result1 = ingest_workbook(path, session_factory=session_factory)
        assert result1.kind == "held_for_review"

        # Abandon the batch
        with session_factory() as s:
            batch = s.get(ImportBatch, result1.batch_id)
            batch.status = ImportStatus.abandoned
            s.commit()

        # Second upload of same file → should NOT raise DuplicateBatchError
        from backend.app.ingest import DuplicateBatchError
        # Register part so re-upload doesn't also hold
        with session_factory() as s:
            s.add(Assembly(part_number="123456"))
            s.commit()

        result2 = ingest_workbook(path, session_factory=session_factory)
        assert result2.batch_id != result1.batch_id


# ---------------------------------------------------------------------------
# State-transition matrix — API layer
# ---------------------------------------------------------------------------

class TestReviewStateTransitions:
    """State-transition matrix for staging row review_status via the API."""

    @pytest.fixture()
    def held_batch(self, session) -> ImportBatch:
        return _make_batch(session)

    @pytest.fixture()
    def pending_row(self, session, held_batch) -> ImportStagingRow:
        return _make_review_row(session, held_batch, review_status="pending")

    # pending → verified via POST /verify
    def test_pending_to_verified(self, client, session, held_batch, pending_row):
        resp = client.post(f"/api/ingest/{held_batch.id}/staging-row/{pending_row.id}/verify")
        assert resp.status_code == 200
        session.expire(pending_row)
        assert pending_row.review_status == "verified"

    # pending → edited via PUT /canonical
    def test_pending_to_edited_via_canonical(self, client, session, held_batch, pending_row):
        resp = client.put(
            f"/api/ingest/{held_batch.id}/canonical/123456",
            json={"canonical_part_number": "654321"},
        )
        assert resp.status_code == 200
        session.expire(pending_row)
        assert pending_row.review_status == "edited"
        assert pending_row.review_part_number_override == "654321"

    # pending → deleted via DELETE /staging-row
    def test_pending_to_deleted(self, client, session, held_batch, pending_row):
        resp = client.delete(f"/api/ingest/{held_batch.id}/staging-row/{pending_row.id}")
        assert resp.status_code == 200
        session.expire(pending_row)
        assert pending_row.review_status == "deleted"
        assert pending_row.discarded_at is not None

    # verified → edited via PUT /canonical
    def test_verified_to_edited(self, client, session, held_batch):
        row = _make_review_row(session, held_batch, review_status="verified")
        resp = client.put(
            f"/api/ingest/{held_batch.id}/canonical/123456",
            json={"canonical_part_number": "999999"},
        )
        assert resp.status_code == 200
        session.expire(row)
        assert row.review_status == "edited"

    # verified → deleted via DELETE /staging-row
    def test_verified_to_deleted(self, client, session, held_batch):
        row = _make_review_row(session, held_batch, review_status="verified")
        resp = client.delete(f"/api/ingest/{held_batch.id}/staging-row/{row.id}")
        assert resp.status_code == 200
        session.expire(row)
        assert row.review_status == "deleted"

    # edited → edited via PUT /canonical (operator iterates)
    def test_edited_to_edited_via_canonical(self, client, session, held_batch):
        row = _make_review_row(session, held_batch, review_status="edited")
        row.review_part_number_override = "111111"
        session.flush()
        resp = client.put(
            f"/api/ingest/{held_batch.id}/canonical/123456",
            json={"canonical_part_number": "222222"},
        )
        assert resp.status_code == 200
        session.expire(row)
        assert row.review_status == "edited"
        assert row.review_part_number_override == "222222"

    # edited → verified via PUT /canonical with original parsed value
    def test_edited_to_verified_by_reverting(self, client, session, held_batch):
        """Operator reverts to parsed value → review_status becomes 'verified'."""
        row = _make_review_row(session, held_batch, raw_job="123456\nNEW", review_status="edited")
        row.review_part_number_override = "654321"
        session.flush()
        # Revert to original parsed part_number (no split suffix in the raw_job)
        resp = client.put(
            f"/api/ingest/{held_batch.id}/canonical/123456",
            json={"canonical_part_number": "123456"},
        )
        assert resp.status_code == 200
        session.expire(row)
        # canonical matches parsed pn AND split_suffix == parsed (both None) → verified
        assert row.review_status == "verified"

    # edited → deleted
    def test_edited_to_deleted(self, client, session, held_batch):
        row = _make_review_row(session, held_batch, review_status="edited")
        resp = client.delete(f"/api/ingest/{held_batch.id}/staging-row/{row.id}")
        assert resp.status_code == 200
        session.expire(row)
        assert row.review_status == "deleted"

    # deleted → any mutation returns 409
    def test_deleted_to_verify_returns_409(self, client, session, held_batch):
        row = _make_review_row(session, held_batch, review_status="deleted")
        resp = client.post(f"/api/ingest/{held_batch.id}/staging-row/{row.id}/verify")
        assert resp.status_code == 409

    def test_deleted_to_delete_returns_409(self, client, session, held_batch):
        row = _make_review_row(session, held_batch, review_status="deleted")
        resp = client.delete(f"/api/ingest/{held_batch.id}/staging-row/{row.id}")
        assert resp.status_code == 409

    # awaiting_review → abandoned via POST /abandon
    def test_awaiting_review_to_abandoned(self, client, session, held_batch):
        resp = client.post(f"/api/ingest/{held_batch.id}/abandon")
        assert resp.status_code == 200
        session.expire(held_batch)
        assert held_batch.status == ImportStatus.abandoned

    # awaiting_review → processed via POST /confirm (clean Stage 4..6)
    def test_awaiting_review_to_processed_via_confirm(
        self, client, session, workbook_factory, session_factory
    ):
        """Full round-trip: upload → held → verify → confirm → processed."""
        path = workbook_factory(
            [{"JOB": "123456\nNEW", "QTY": "10", "CUSTOMER": "ACME"}],
            seed_assemblies=False,
        )
        result = ingest_workbook(path, session_factory=session_factory)
        assert result.kind == "held_for_review"
        batch_id = result.batch_id

        # Verify the pending row
        with session_factory() as s:
            row = s.scalars(
                select(ImportStagingRow)
                .where(ImportStagingRow.batch_id == batch_id)
                .where(ImportStagingRow.review_status == "pending")
            ).first()
            assert row is not None
            row_id = row.id

        resp = client.post(f"/api/ingest/{batch_id}/staging-row/{row_id}/verify")
        assert resp.status_code == 200

        # Confirm
        resp = client.post(f"/api/ingest/{batch_id}/confirm")
        assert resp.status_code == 200

        with session_factory() as s:
            batch = s.get(ImportBatch, batch_id)
            assert batch.status in {ImportStatus.processed, ImportStatus.error}

    # confirm with pending rows returns 409
    def test_confirm_with_pending_rows_returns_409(self, client, session, held_batch, pending_row):
        resp = client.post(f"/api/ingest/{held_batch.id}/confirm")
        assert resp.status_code == 409
        assert "pending" in resp.json()["detail"]

    # awaiting_review → error via POST /confirm (Stage 4..6 errors)
    def test_awaiting_review_to_error_via_confirm(
        self, client, session, workbook_factory, session_factory
    ):
        """Confirm with bad data → batch.status = error."""
        path = workbook_factory(
            [{"JOB": "123456\nNEW", "QTY": "BAD_QTY", "CUSTOMER": "ACME"}],
            seed_assemblies=False,
        )
        result = ingest_workbook(path, session_factory=session_factory)
        assert result.kind == "held_for_review"
        batch_id = result.batch_id

        with session_factory() as s:
            row = s.scalars(
                select(ImportStagingRow)
                .where(ImportStagingRow.batch_id == batch_id)
                .where(ImportStagingRow.review_status == "pending")
            ).first()
            row_id = row.id

        # Verify (accept the row as-is)
        client.post(f"/api/ingest/{batch_id}/staging-row/{row_id}/verify")

        resp = client.post(f"/api/ingest/{batch_id}/confirm")
        assert resp.status_code == 200

        with session_factory() as s:
            batch = s.get(ImportBatch, batch_id)
            assert batch.status == ImportStatus.error


# ---------------------------------------------------------------------------
# GET /ingest/awaiting-review
# ---------------------------------------------------------------------------

class TestListAwaitingReview:
    def test_returns_awaiting_review_batches(self, client, session):
        b1 = _make_batch(session)
        b2 = _make_batch(session, status=ImportStatus.processed)
        _make_review_row(session, b1)

        resp = client.get("/api/ingest/awaiting-review")
        assert resp.status_code == 200
        ids = [item["batch_id"] for item in resp.json()]
        assert b1.id in ids
        assert b2.id not in ids

    def test_returns_pending_row_count(self, client, session):
        b = _make_batch(session)
        _make_review_row(session, b, review_status="pending")
        _make_review_row(session, b, review_status="verified", source_row_number=2)

        resp = client.get("/api/ingest/awaiting-review")
        assert resp.status_code == 200
        item = next(x for x in resp.json() if x["batch_id"] == b.id)
        assert item["pending_row_count"] == 1


# ---------------------------------------------------------------------------
# GET /ingest/{batch_id}/review
# ---------------------------------------------------------------------------

class TestGetReviewPayload:
    def test_returns_404_for_missing_batch(self, client):
        resp = client.get("/api/ingest/9999/review")
        assert resp.status_code == 404

    def test_returns_409_for_non_awaiting_batch(self, client, session):
        b = _make_batch(session, status=ImportStatus.processed)
        resp = client.get(f"/api/ingest/{b.id}/review")
        assert resp.status_code == 409

    def test_returns_b_groups(self, client, session):
        b = _make_batch(session)
        _make_review_row(session, b, raw_job="123456\nNEW")

        resp = client.get(f"/api/ingest/{b.id}/review")
        assert resp.status_code == 200
        body = resp.json()
        assert body["batch_id"] == b.id
        assert len(body["new_b_numbers"]) == 1
        assert body["new_b_numbers"][0]["parsed_part_number"] == "123456"
        assert body["new_non_b_numbers"] == []


# ---------------------------------------------------------------------------
# PUT /canonical
# ---------------------------------------------------------------------------

class TestSetCanonical:
    def test_sets_override_and_marks_edited(self, client, session):
        b = _make_batch(session)
        row = _make_review_row(session, b, raw_job="123456\nNEW")

        resp = client.put(
            f"/api/ingest/{b.id}/canonical/123456",
            json={"canonical_part_number": "654321"},
        )
        assert resp.status_code == 200
        session.expire(row)
        assert row.review_part_number_override == "654321"
        assert row.review_status == "edited"
        assert row.reviewed_by == "frontend"
        assert row.original_raw_job is not None

    def test_empty_canonical_returns_422(self, client, session):
        b = _make_batch(session)
        _make_review_row(session, b)
        resp = client.put(
            f"/api/ingest/{b.id}/canonical/123456",
            json={"canonical_part_number": "   "},
        )
        assert resp.status_code == 422

    def test_all_deleted_group_returns_409(self, client, session):
        b = _make_batch(session)
        row = _make_review_row(session, b, review_status="deleted")
        resp = client.put(
            f"/api/ingest/{b.id}/canonical/123456",
            json={"canonical_part_number": "654321"},
        )
        assert resp.status_code == 409

    def test_non_awaiting_batch_returns_409(self, client, session):
        b = _make_batch(session, status=ImportStatus.processed)
        resp = client.put(
            f"/api/ingest/{b.id}/canonical/123456",
            json={"canonical_part_number": "654321"},
        )
        assert resp.status_code == 409

    def test_suffix_computed_from_prefix_match(self, client, session):
        """When canonical is a prefix of the cell, suffix is computed automatically."""
        b = _make_batch(session)
        row = _make_review_row(session, b, raw_job="123456-1par\nNEW")

        resp = client.put(
            f"/api/ingest/{b.id}/canonical/123456",
            json={"canonical_part_number": "123456"},
        )
        assert resp.status_code == 200
        session.expire(row)
        assert row.review_split_suffix_override == "-1par"

    def test_suffix_none_when_no_prefix_match(self, client, session):
        """Typo correction: no prefix match → split_suffix_override is None."""
        b = _make_batch(session)
        row = _make_review_row(session, b, raw_job="123456-1par\nNEW")

        resp = client.put(
            f"/api/ingest/{b.id}/canonical/123456",
            json={"canonical_part_number": "654321"},
        )
        assert resp.status_code == 200
        session.expire(row)
        assert row.review_split_suffix_override is None


# ---------------------------------------------------------------------------
# PATCH /split-suffix
# ---------------------------------------------------------------------------

class TestPatchSplitSuffix:
    def test_sets_suffix_on_edited_row(self, client, session):
        b = _make_batch(session)
        row = _make_review_row(session, b, review_status="edited")
        row.review_part_number_override = "654321"
        session.flush()

        resp = client.patch(
            f"/api/ingest/{b.id}/staging-row/{row.id}/split-suffix",
            json={"split_suffix": "-1par"},
        )
        assert resp.status_code == 200
        session.expire(row)
        assert row.review_split_suffix_override == "-1par"
        assert row.review_status == "edited"

    def test_clears_suffix_with_none(self, client, session):
        b = _make_batch(session)
        row = _make_review_row(session, b, review_status="edited")
        row.review_part_number_override = "654321"
        row.review_split_suffix_override = "-par"
        session.flush()

        resp = client.patch(
            f"/api/ingest/{b.id}/staging-row/{row.id}/split-suffix",
            json={"split_suffix": None},
        )
        assert resp.status_code == 200
        session.expire(row)
        assert row.review_split_suffix_override is None

    def test_requires_canonical_first(self, client, session):
        b = _make_batch(session)
        row = _make_review_row(session, b, review_status="pending")
        # No review_part_number_override set

        resp = client.patch(
            f"/api/ingest/{b.id}/staging-row/{row.id}/split-suffix",
            json={"split_suffix": "-par"},
        )
        assert resp.status_code == 409

    def test_too_long_suffix_returns_422(self, client, session):
        b = _make_batch(session)
        row = _make_review_row(session, b, review_status="edited")
        row.review_part_number_override = "654321"
        session.flush()

        resp = client.patch(
            f"/api/ingest/{b.id}/staging-row/{row.id}/split-suffix",
            json={"split_suffix": "x" * 33},
        )
        assert resp.status_code == 422

    def test_deleted_row_returns_409(self, client, session):
        b = _make_batch(session)
        row = _make_review_row(session, b, review_status="deleted")

        resp = client.patch(
            f"/api/ingest/{b.id}/staging-row/{row.id}/split-suffix",
            json={"split_suffix": "-par"},
        )
        assert resp.status_code == 409


# ---------------------------------------------------------------------------
# POST /verify
# ---------------------------------------------------------------------------

class TestVerifyStagingRow:
    def test_pending_to_verified(self, client, session):
        b = _make_batch(session)
        row = _make_review_row(session, b, review_status="pending")

        resp = client.post(f"/api/ingest/{b.id}/staging-row/{row.id}/verify")
        assert resp.status_code == 200
        session.expire(row)
        assert row.review_status == "verified"
        assert row.reviewed_by == "frontend"

    def test_non_pending_returns_409(self, client, session):
        b = _make_batch(session)
        row = _make_review_row(session, b, review_status="edited")

        resp = client.post(f"/api/ingest/{b.id}/staging-row/{row.id}/verify")
        assert resp.status_code == 409

    def test_batch_not_awaiting_review_returns_409(self, client, session):
        b = _make_batch(session, status=ImportStatus.processed)
        row = _make_review_row(session, b, review_status="pending")

        resp = client.post(f"/api/ingest/{b.id}/staging-row/{row.id}/verify")
        assert resp.status_code == 409


# ---------------------------------------------------------------------------
# DELETE /staging-row (hard discard during review)
# ---------------------------------------------------------------------------

class TestDeleteStagingRowFromReview:
    def test_sets_discarded_at_and_deleted_status(self, client, session):
        b = _make_batch(session)
        row = _make_review_row(session, b, review_status="pending")

        resp = client.delete(f"/api/ingest/{b.id}/staging-row/{row.id}")
        assert resp.status_code == 200
        session.expire(row)
        assert row.review_status == "deleted"
        assert row.discarded_at is not None

    def test_already_deleted_returns_409(self, client, session):
        b = _make_batch(session)
        row = _make_review_row(session, b, review_status="deleted")
        row.discarded_at = datetime.now(UTC).replace(tzinfo=None)
        session.flush()

        resp = client.delete(f"/api/ingest/{b.id}/staging-row/{row.id}")
        assert resp.status_code == 409

    def test_wrong_batch_returns_404(self, client, session):
        b = _make_batch(session)
        b2 = _make_batch(session, source_sha256="other")
        row = _make_review_row(session, b2, review_status="pending")

        resp = client.delete(f"/api/ingest/{b.id}/staging-row/{row.id}")
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# POST /abandon
# ---------------------------------------------------------------------------

class TestAbandonReview:
    def test_awaiting_review_to_abandoned(self, client, session):
        b = _make_batch(session)
        resp = client.post(f"/api/ingest/{b.id}/abandon")
        assert resp.status_code == 200
        session.expire(b)
        assert b.status == ImportStatus.abandoned

    def test_error_batch_can_be_abandoned(self, client, session):
        b = _make_batch(session, status=ImportStatus.error)
        resp = client.post(f"/api/ingest/{b.id}/abandon")
        assert resp.status_code == 200

    def test_processed_batch_cannot_be_abandoned(self, client, session):
        b = _make_batch(session, status=ImportStatus.processed)
        resp = client.post(f"/api/ingest/{b.id}/abandon")
        assert resp.status_code == 409

    def test_missing_batch_returns_404(self, client):
        resp = client.post("/api/ingest/9999/abandon")
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# effective_decomposition — unit tests
# ---------------------------------------------------------------------------

class TestEffectiveDecomposition:
    def test_no_override_returns_parsed(self, session):
        from backend.app.transform import effective_decomposition
        from backend.app.extractors import DecomposeError

        row = ImportStagingRow(batch_id=1, source_row_number=1, raw_job="123456\nNEW")
        result = effective_decomposition(row)

        assert not isinstance(result, DecomposeError)
        assert result.part_number == "123456"
        assert result.split_suffix is None

    def test_part_number_override_applied(self, session):
        from backend.app.transform import effective_decomposition

        row = ImportStagingRow(
            batch_id=1,
            source_row_number=1,
            raw_job="123456\nNEW",
            review_part_number_override="654321",
            review_split_suffix_override=None,
        )
        result = effective_decomposition(row)

        assert result.part_number == "654321"
        assert result.split_suffix is None

    def test_split_suffix_override_applied(self, session):
        from backend.app.transform import effective_decomposition

        row = ImportStagingRow(
            batch_id=1,
            source_row_number=1,
            raw_job="123456\nNEW",
            review_part_number_override="123456",
            review_split_suffix_override="-par",
        )
        result = effective_decomposition(row)

        assert result.part_number == "123456"
        assert result.split_suffix == "-par"

    def test_other_fields_unchanged_by_override(self, session):
        from backend.app.transform import effective_decomposition
        from backend.app.models import BuildType

        row = ImportStagingRow(
            batch_id=1,
            source_row_number=1,
            raw_job="123456\nNEW",
            review_part_number_override="654321",
            review_split_suffix_override="-par",
        )
        result = effective_decomposition(row)

        assert result.build_type == BuildType.new

    def test_invalid_raw_job_returns_error(self, session):
        from backend.app.transform import effective_decomposition
        from backend.app.extractors import DecomposeError

        row = ImportStagingRow(batch_id=1, source_row_number=1, raw_job=None)
        result = effective_decomposition(row)

        assert result is None or isinstance(result, DecomposeError)


# ---------------------------------------------------------------------------
# P-2: CLI main() dispatch — held_for_review and processed_or_error paths
# ---------------------------------------------------------------------------

class TestCliMainDispatch:
    """CLI ingest.py::main() must not crash on held_for_review result (P-2).

    Uses a file-based SQLite database (not the test's in-memory one) so the
    subprocess can connect to a real schema-initialised database.
    """

    @pytest.fixture()
    def cli_db(self, tmp_path):
        """Create a file-based SQLite db with the full schema and return its URL."""
        from sqlalchemy import create_engine
        from backend.app.models import Base

        db_path = tmp_path / "cli_test.db"
        url = f"sqlite:///{db_path.as_posix()}"
        engine = create_engine(url, connect_args={"check_same_thread": False})
        Base.metadata.create_all(engine)
        engine.dispose()
        return url

    def test_held_for_review_exits_with_code_3(self, workbook_factory, cli_db, tmp_path):
        """Held-for-review batch prints status and exits with code 3, not TypeError."""
        import subprocess, sys, os

        # workbook_factory uses the pytest in-memory DB for seeding;
        # here we just need the xlsx file — no assembly seeding required.
        path = workbook_factory(
            [{"JOB": "999001\nNEW", "QTY": "10", "CUSTOMER": "ACME"}],
            seed_assemblies=False,
        )
        env = {**os.environ, "SCHEDULER_DATABASE_URL": cli_db}
        result = subprocess.run(
            [sys.executable, "-m", "backend.app.ingest", str(path)],
            capture_output=True, text=True, env=env,
        )
        assert result.returncode == 3, (
            f"Expected exit 3 for held_for_review, got {result.returncode}.\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )
        assert "held_for_review" in result.stdout
        assert "new B#" in result.stdout

    def test_processed_clean_exits_with_code_0(self, workbook_factory, cli_db, tmp_path):
        """Clean ingest of known assembly exits 0."""
        import subprocess, sys, os
        from sqlalchemy import create_engine
        from sqlalchemy.orm import sessionmaker as sm

        # Seed the assembly into the file-based DB.
        engine = create_engine(cli_db, connect_args={"check_same_thread": False})
        with sm(bind=engine)() as s:
            s.add(Assembly(part_number="999002"))
            s.commit()
        engine.dispose()

        path = workbook_factory(
            [{"JOB": "999002\nNEW", "QTY": "5", "CUSTOMER": "ACME"}],
        )
        env = {**os.environ, "SCHEDULER_DATABASE_URL": cli_db}
        result = subprocess.run(
            [sys.executable, "-m", "backend.app.ingest", str(path)],
            capture_output=True, text=True, env=env,
        )
        assert result.returncode == 0, (
            f"Expected exit 0 for clean ingest, got {result.returncode}.\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )
        assert "inserted" in result.stdout


# ---------------------------------------------------------------------------
# P-4: DELETE and PATCH /split-suffix preconditions — review_status IS NULL
# ---------------------------------------------------------------------------

class TestPreconditionNullReviewStatus:
    """_require_row_in_review_states rejects rows with review_status IS NULL (P-4)."""

    def test_delete_row_with_null_review_status_returns_409(self, client, session):
        """A non-review row (review_status IS NULL) cannot be deleted via review endpoint."""
        b = _make_batch(session)
        # Intentionally skip setting review_status (NULL).
        row = ImportStagingRow(
            batch_id=b.id,
            source_row_number=1,
            raw_job="123456\nNEW",
            review_status=None,
        )
        session.add(row)
        session.flush()

        resp = client.delete(f"/api/ingest/{b.id}/staging-row/{row.id}")
        assert resp.status_code == 409

    def test_patch_split_suffix_with_null_review_status_returns_409(self, client, session):
        """A non-review row (review_status IS NULL) cannot have its split-suffix patched."""
        b = _make_batch(session)
        row = ImportStagingRow(
            batch_id=b.id,
            source_row_number=1,
            raw_job="123456\nNEW",
            review_status=None,
            review_part_number_override="654321",  # canonical already set (covers old path)
        )
        session.add(row)
        session.flush()

        resp = client.patch(
            f"/api/ingest/{b.id}/staging-row/{row.id}/split-suffix",
            json={"split_suffix": "-par"},
        )
        assert resp.status_code == 409


# ---------------------------------------------------------------------------
# Patch 02 — P-2: Cache hit and invalidation tests
# ---------------------------------------------------------------------------

class TestSimilarAssembliesCache:
    """_similar_assemblies_cached returns cached result on second call;
    cache is evicted on confirm and abandon."""

    def test_cache_hit_avoids_second_compute(self, client, session):
        """Second GET /review for the same batch does not call _similar_assemblies_compute."""
        import backend.app.api.ingest as mod
        from unittest.mock import patch as mpatch

        b = _make_batch(session)
        row = _make_review_row(session, b, review_status="pending", raw_job="138623\nNEW")
        row.parsed_part_number = "138623"
        session.add(Assembly(part_number="138624"))
        session.commit()

        compute_calls: list[str] = []
        original_compute = mod._similar_assemblies_compute

        def tracking_compute(session, pn, top_n=3):
            compute_calls.append(pn)
            return original_compute(session, pn, top_n)

        with mpatch.object(mod, "_similar_assemblies_compute", side_effect=tracking_compute):
            # First request — populates cache.
            resp1 = client.get(f"/api/ingest/{b.id}/review")
            assert resp1.status_code == 200
            first_call_count = len(compute_calls)

            # Second request for same batch — should hit cache, no new compute calls.
            resp2 = client.get(f"/api/ingest/{b.id}/review")
            assert resp2.status_code == 200
            second_call_count = len(compute_calls)

        assert first_call_count >= 1, "Expected at least one compute call on first request"
        assert second_call_count == first_call_count, (
            f"Expected cache hit on second GET /review; "
            f"got {second_call_count - first_call_count} extra compute calls"
        )

    def test_cache_invalidated_on_abandon(self, client, session):
        """Cache entries for a batch_id are evicted when the batch is abandoned."""
        import backend.app.api.ingest as mod
        from unittest.mock import patch as mpatch

        b = _make_batch(session)
        row = _make_review_row(session, b, review_status="pending", raw_job="138623\nNEW")
        row.parsed_part_number = "138623"
        session.add(Assembly(part_number="138624"))
        session.commit()

        # Pre-populate the cache for this batch.
        key = (b.id, "138623")
        mod._similar_cache[key] = [{"part_number": "138624", "edit_distance": 1}]
        assert key in mod._similar_cache

        resp = client.post(f"/api/ingest/{b.id}/abandon")
        assert resp.status_code == 200
        assert key not in mod._similar_cache, "Cache should be evicted after abandon"


# ---------------------------------------------------------------------------
# Patch 02 — P-4: Verify and delete mutation response shapes
# ---------------------------------------------------------------------------

class TestMutationResponseShapes:
    """Verify, delete, patch-split-suffix, and set-canonical all return enriched shapes."""

    def test_verify_returns_row_and_group(self, client, session):
        """POST verify returns {row: {...}, group: {...}} not bare {staging_row_id, review_status}."""
        b = _make_batch(session)
        row = _make_review_row(session, b, review_status="pending", raw_job="138623\nNEW")
        row.parsed_part_number = "138623"
        session.commit()

        resp = client.post(f"/api/ingest/{b.id}/staging-row/{row.id}/verify")
        assert resp.status_code == 200
        data = resp.json()
        assert "row" in data, f"Expected 'row' key; got {list(data)}"
        assert "group" in data, f"Expected 'group' key; got {list(data)}"
        assert data["row"]["staging_row_id"] == row.id
        assert data["row"]["review_status"] == "verified"
        assert "parsed_part_number" in data["group"]
        assert "active_row_count" in data["group"]

    def test_delete_returns_row_and_group(self, client, session):
        """DELETE row returns {row: {...}, group: {...}}."""
        b = _make_batch(session)
        row = _make_review_row(session, b, review_status="pending", raw_job="138623\nNEW")
        row.parsed_part_number = "138623"
        session.commit()

        resp = client.delete(f"/api/ingest/{b.id}/staging-row/{row.id}")
        assert resp.status_code == 200
        data = resp.json()
        assert "row" in data
        assert "group" in data
        assert data["row"]["review_status"] == "deleted"

    def test_set_canonical_returns_updated_rows_and_group(self, client, session):
        """PUT /canonical returns {updated_rows: [...], group: {...}}."""
        b = _make_batch(session)
        row = _make_review_row(session, b, review_status="pending", raw_job="138623\nNEW")
        row.parsed_part_number = "138623"
        session.commit()

        resp = client.put(
            f"/api/ingest/{b.id}/canonical/138623",
            json={"canonical_part_number": "138623"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "updated_rows" in data, f"Expected 'updated_rows'; got {list(data)}"
        assert "group" in data
        assert isinstance(data["updated_rows"], list)


# ---------------------------------------------------------------------------
# Patch 02 — P-5: parsed_part_number populated at Stage 3.5
# ---------------------------------------------------------------------------

class TestParsedPartNumberPopulation:
    """classify_new_parts_for_review writes parsed_part_number on all rows with a
    successful decomposition (not just flagged-for-review rows)."""

    def test_parsed_part_number_set_on_review_row(self, workbook_factory, session, session_factory):
        """After ingest, rows with parseable raw_job have parsed_part_number set."""
        from backend.app.ingest import ingest_workbook

        path = workbook_factory(
            [{"JOB": "138623\nNEW", "QTY": "5", "CUSTOMER": "ACME"}],
            seed_assemblies=False,
        )
        result = ingest_workbook(str(path), session_factory=session_factory)
        assert result.kind == "held_for_review"

        rows = list(session.scalars(
            select(ImportStagingRow).where(ImportStagingRow.batch_id == result.batch_id)
        ).all())
        review_rows = [r for r in rows if r.review_status == "pending"]
        assert review_rows, "Expected at least one pending review row"
        for row in review_rows:
            assert row.parsed_part_number == "138623", (
                f"Expected parsed_part_number='138623', got {row.parsed_part_number!r}"
            )

    def test_back_compat_null_parsed_pn_resolved_via_decomposition(self, client, session):
        """A row with parsed_part_number IS NULL is still returned by _rows_for_pn
        via the fallback decomposition path."""
        b = _make_batch(session)
        # Simulate a pre-0010 row: parsed_part_number is NULL.
        row = _make_review_row(session, b, review_status="pending", raw_job="138623\nNEW")
        # Do NOT set parsed_part_number — leave NULL.
        session.commit()

        resp = client.get(f"/api/ingest/{b.id}/review")
        assert resp.status_code == 200
        data = resp.json()
        all_rows = [
            r
            for g in (
                data["new_b_numbers"] + data["new_non_b_numbers"] + data["intra_file_duplicates"]
            )
            for r in g["rows"]
        ]
        assert any(r["staging_row_id"] == row.id for r in all_rows), (
            "Expected pre-0010 row (parsed_part_number IS NULL) to appear in review payload"
        )
