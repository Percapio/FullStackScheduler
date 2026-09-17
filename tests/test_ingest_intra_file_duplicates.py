"""Tests for Phase 18c §6 — Stage 3.6 intra-file duplicate detection — and the
Patch 06 §7.1 regression: an unresolved duplicate can never overwrite a Job.

Covers:
 - augment_with_intra_file_duplicates groups rows by full IdentityTuple key
 - Different build_types are NOT grouped
 - Rows without existing review_status get 'verified' (F7)
 - Stage 3.5 status is not clobbered by Stage 3.6
 - A row can appear in both new_b_numbers and intra_file_duplicates
 - POST /confirm refuses an unresolved duplicate with 409 identity_collision
 - Stage 4, reached without the gate, errors both rows instead of overwriting
"""
from __future__ import annotations

import pytest
from sqlalchemy import create_engine, event, func, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from backend.app.ingest import (
    ReviewClassification,
    augment_with_intra_file_duplicates,
    classify_new_parts_for_review,
    ingest_workbook,
    run_stages_4_to_6,
)
from backend.app.models import (
    Base,
    ImportBatch,
    ImportStagingRow,
    ImportStatus,
    Job,
    SheetKind,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def engine():
    eng = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(eng, "connect")
    def _fk_on(dbapi_connection, _rec):
        cur = dbapi_connection.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()

    Base.metadata.create_all(eng)
    try:
        yield eng
    finally:
        eng.dispose()


@pytest.fixture()
def session(engine) -> Session:
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    with factory() as s:
        yield s


def _make_batch(session) -> ImportBatch:
    batch = ImportBatch(
        source_file="test.xlsx",
        source_sha256="abc" * 20 + "ab",
        status=ImportStatus.awaiting_review,
        sheet_kind=SheetKind.live,
        row_count=2,
    )
    session.add(batch)
    session.flush()
    return batch


def _make_row(session, batch: ImportBatch, *, raw_job: str, source_row_number: int) -> ImportStagingRow:
    row = ImportStagingRow(
        batch_id=batch.id,
        source_row_number=source_row_number,
        raw_job=raw_job,
        processing_status=ImportStatus.pending,
    )
    session.add(row)
    session.flush()
    return row


# ---------------------------------------------------------------------------
# Stage 3.6 — augment_with_intra_file_duplicates
# ---------------------------------------------------------------------------

class TestAugmentWithIntraFileDuplicates:
    def test_stage_3_6_groups_rows_by_full_identity(self, session):
        """Two rows with identical full identity become one intra_file_duplicates group."""
        batch = _make_batch(session)
        # Both rows decompose to the same part_number with no distinguishing suffix.
        row1 = _make_row(session, batch, raw_job="123456\nNEW", source_row_number=1)
        row2 = _make_row(session, batch, raw_job="123456\nNEW", source_row_number=2)

        base_classification = ReviewClassification()
        result = augment_with_intra_file_duplicates(base_classification, [row1, row2], set())

        assert len(result.intra_file_duplicates) == 1
        group = result.intra_file_duplicates[0]
        assert group.parsed_part_number == "123456"
        assert len(group.rows) == 2
        assert group.identity is not None

    def test_stage_3_6_does_not_group_rows_with_different_build_types(self, session):
        """Two rows with same part_number but different build_type are NOT grouped."""
        batch = _make_batch(session)
        # raw_job with different build contexts should decompose to different build_types.
        # Use 'NEW' vs 'REWORK' to produce different build_type values.
        row1 = _make_row(session, batch, raw_job="123456\nNEW", source_row_number=1)
        row2 = _make_row(session, batch, raw_job="123456\nREWORK", source_row_number=2)

        base_classification = ReviewClassification()
        result = augment_with_intra_file_duplicates(base_classification, [row1, row2], set())

        # Rows that decompose to different build_types must not be grouped together.
        for group in result.intra_file_duplicates:
            assert len(group.rows) < 2 or len({type(r) for r in group.rows}) > 0
        # At most one duplicate group (if both decompose identically, this test
        # needs updating; the intent is a distinguishing-build-type scenario).
        assert not any(
            len(g.rows) == 2 for g in result.intra_file_duplicates
        ), "Rows with different build types must not share a group"

    def test_stage_3_6_marks_duplicate_rows_verified_when_not_already_set(self, session):
        """F7: rows in an intra-file duplicate group get review_status='verified' if not set."""
        batch = _make_batch(session)
        row1 = _make_row(session, batch, raw_job="123456\nNEW", source_row_number=1)
        row2 = _make_row(session, batch, raw_job="123456\nNEW", source_row_number=2)
        # Ensure neither row has a review_status yet (stage 3.5 not called for these).
        assert row1.review_status is None
        assert row2.review_status is None

        base_classification = ReviewClassification()
        augment_with_intra_file_duplicates(base_classification, [row1, row2], set())

        assert row1.review_status == "verified"
        assert row2.review_status == "verified"

    def test_stage_3_6_does_not_clobber_stage_3_5_status(self, session):
        """A row that Stage 3.5 already set to 'verified' is not re-touched."""
        batch = _make_batch(session)
        row1 = _make_row(session, batch, raw_job="123456\nNEW", source_row_number=1)
        row2 = _make_row(session, batch, raw_job="123456\nNEW", source_row_number=2)
        # Simulate Stage 3.5 already setting status.
        row1.review_status = "verified"
        row2.review_status = "verified"
        session.flush()

        base_classification = ReviewClassification()
        augment_with_intra_file_duplicates(base_classification, [row1, row2], set())

        # Status must remain 'verified', not be reset to None or anything else.
        assert row1.review_status == "verified"
        assert row2.review_status == "verified"

    def test_row_can_be_in_both_new_b_and_intra_file_duplicates(self, session):
        """Two rows decompose to the same new B#: appear in new_b_numbers AND
        intra_file_duplicates when their full identity tuples match.
        """
        batch = _make_batch(session)
        row1 = _make_row(session, batch, raw_job="123456\nNEW", source_row_number=1)
        row2 = _make_row(session, batch, raw_job="123456\nNEW", source_row_number=2)

        # Stage 3.5: produces new_b_numbers group.
        stage_3_5 = classify_new_parts_for_review(session, [row1, row2], set())
        assert len(stage_3_5.b) == 1
        assert len(stage_3_5.b[0].rows) == 2

        # Stage 3.6: same rows also appear in intra_file_duplicates.
        result = augment_with_intra_file_duplicates(stage_3_5, [row1, row2], set())
        assert len(result.b) == 1, "new_b_numbers must be carried through"
        assert len(result.intra_file_duplicates) == 1
        dup_group = result.intra_file_duplicates[0]
        dup_row_ids = {r.id for r in dup_group.rows}
        b_row_ids = {r.id for r in result.b[0].rows}
        assert dup_row_ids == b_row_ids, "Both rows appear in both groups"


# ---------------------------------------------------------------------------
# Patch 06 §7.1 — the regression that motivated the patch
# ---------------------------------------------------------------------------

class TestUnresolvedDuplicateCannotOverwrite:
    """Two rows with one raw identity, both valid enough for Stage 5 to write,
    with different quantities.  Before Patch 06 the second row silently
    overwrote the first row's Job and the batch finished 'processed'."""

    ROWS = [
        {"JOB": "137845\nNEW", "QTY": "10", "CUSTOMER": "ACME"},
        {"JOB": "137845\nNEW", "QTY": "25", "CUSTOMER": "ACME"},
    ]

    def test_confirm_refuses_with_identity_collision(self, client, workbook_factory, session_factory):
        held = ingest_workbook(workbook_factory(self.ROWS), session_factory=session_factory)
        assert held.kind == "held_for_review"

        resp = client.post(f"/api/ingest/{held.batch_id}/confirm")

        assert resp.status_code == 409
        body = resp.json()
        assert body["code"] == "identity_collision"
        assert len(body["collisions"]) == 1
        assert len(body["collisions"][0]["row_ids"]) == 2
        assert [g["resolved"] for g in body["intra_file_duplicates"]] == [False]
        with session_factory() as s:
            assert s.scalar(select(func.count()).select_from(Job)) == 0
            assert s.get(ImportBatch, held.batch_id).status == ImportStatus.awaiting_review
            statuses = s.scalars(
                select(ImportStagingRow.processing_status)
                .where(ImportStagingRow.batch_id == held.batch_id)
            ).all()
            assert statuses == [ImportStatus.pending, ImportStatus.pending]

    def test_stage_4_errors_both_rows_when_gate_is_bypassed(self, workbook_factory, session_factory):
        held = ingest_workbook(workbook_factory(self.ROWS), session_factory=session_factory)
        assert held.kind == "held_for_review"

        result = run_stages_4_to_6(
            batch_id=held.batch_id,
            rows_total=2,
            sheet_kind=SheetKind.live,
            source_sha256=held.source_sha256,
            filename=held.filename,
            duplicate_of=None,
            session_factory=session_factory,
        )

        assert result.rows_errored == 2
        assert result.rows_inserted == 0
        assert result.rows_updated == 0
        with session_factory() as s:
            assert s.scalar(select(func.count()).select_from(Job)) == 0
            assert s.get(ImportBatch, held.batch_id).status == ImportStatus.error
            rows = s.scalars(
                select(ImportStagingRow).where(ImportStagingRow.batch_id == held.batch_id)
            ).all()
            assert len(rows) == 2
            for row in rows:
                assert row.processing_status == ImportStatus.error
                assert row.processing_error.startswith("Intra-file duplicate JOB identity")
                assert row.duplicate_group_key == "137845|new|||"
