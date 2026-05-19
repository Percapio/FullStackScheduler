"""Tests for Phase 18c §6 — Stage 3.6 intra-file duplicate detection and Stage 4 flag.

Covers:
 - augment_with_intra_file_duplicates groups rows by full IdentityTuple key
 - Different build_types are NOT grouped
 - Rows without existing review_status get 'verified' (F7)
 - Stage 3.5 status is not clobbered by Stage 3.6
 - A row can appear in both new_b_numbers and intra_file_duplicates
 - Feature flag false (default): Stage 4 passes intra-file duplicates through
 - Feature flag true: Stage 4 errors on intra-file duplicates
"""
from __future__ import annotations

from unittest.mock import patch

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from backend.app.config import Settings
from backend.app.ingest import (
    ReviewClassification,
    augment_with_intra_file_duplicates,
    classify_new_parts_for_review,
)
from backend.app.models import (
    Base,
    ImportBatch,
    ImportStagingRow,
    ImportStatus,
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
# Stage 4 feature flag
# ---------------------------------------------------------------------------

class TestIntraFileCollisionFlag:
    def test_intra_file_collision_flag_off_passes_stage_4(self, session):
        """With the legacy flag false (default), Stage 4 does not error on
        intra-file collisions — no duplicate_group_key is set on rows.
        """
        from backend.app.ingest import run_stages_4_to_6
        from backend.app.models import SheetKind
        from sqlalchemy.orm import sessionmaker as sm

        batch = ImportBatch(
            source_file="test.xlsx",
            source_sha256="a" * 64,
            status=ImportStatus.awaiting_review,
            sheet_kind=SheetKind.live,
            row_count=2,
        )
        session.add(batch)
        session.flush()

        row1 = ImportStagingRow(
            batch_id=batch.id,
            source_row_number=1,
            raw_job="123456\nNEW",
            processing_status=ImportStatus.pending,
        )
        row2 = ImportStagingRow(
            batch_id=batch.id,
            source_row_number=2,
            raw_job="123456\nNEW",
            processing_status=ImportStatus.pending,
        )
        session.add_all([row1, row2])
        session.commit()

        engine = session.get_bind()
        factory = sm(bind=engine, autoflush=False, expire_on_commit=False)

        def _factory():
            return factory()

        run_stages_4_to_6(
            batch_id=batch.id,
            rows_total=2,
            sheet_kind=SheetKind.live,
            source_sha256="a" * 64,
            filename="test.xlsx",
            duplicate_of=None,
            session_factory=_factory,
        )

        # After run_stages_4_to_6, reload rows to verify Stage 4 did NOT set collision errors.
        from sqlalchemy import select as sa_select
        with factory() as s:
            rows = s.scalars(
                sa_select(ImportStagingRow).where(ImportStagingRow.batch_id == batch.id)
            ).all()
            collision_errors = [
                r for r in rows
                if r.processing_error and "Intra-file duplicate" in r.processing_error
            ]
        assert collision_errors == [], (
            "Stage 4 must not set intra-file collision errors when flag is False"
        )

    def test_intra_file_collision_flag_on_errors_stage_4(self, session):
        """With the legacy flag true, Stage 4 sets intra-file collision errors on rows."""
        from backend.app.ingest import run_stages_4_to_6
        from backend.app.models import SheetKind
        from sqlalchemy import select as sa_select
        from sqlalchemy.orm import sessionmaker as sm

        batch = ImportBatch(
            source_file="test2.xlsx",
            source_sha256="b" * 64,
            status=ImportStatus.awaiting_review,
            sheet_kind=SheetKind.live,
            row_count=2,
        )
        session.add(batch)
        session.flush()

        row1 = ImportStagingRow(
            batch_id=batch.id,
            source_row_number=1,
            raw_job="654321\nNEW",
            processing_status=ImportStatus.pending,
        )
        row2 = ImportStagingRow(
            batch_id=batch.id,
            source_row_number=2,
            raw_job="654321\nNEW",
            processing_status=ImportStatus.pending,
        )
        session.add_all([row1, row2])
        session.commit()

        engine = session.get_bind()
        factory = sm(bind=engine, autoflush=False, expire_on_commit=False)

        def _factory():
            return factory()

        overridden_settings = Settings(intra_file_collision_legacy_error_path=True)
        with patch("backend.app.ingest.get_settings", return_value=overridden_settings):
            run_stages_4_to_6(
                batch_id=batch.id,
                rows_total=2,
                sheet_kind=SheetKind.live,
                source_sha256="b" * 64,
                filename="test2.xlsx",
                duplicate_of=None,
                session_factory=_factory,
            )

        with factory() as s:
            rows = s.scalars(
                sa_select(ImportStagingRow).where(ImportStagingRow.batch_id == batch.id)
            ).all()
            collision_errors = [
                r for r in rows
                if r.processing_error and "Intra-file duplicate" in r.processing_error
            ]
        assert len(collision_errors) == 2, (
            "Stage 4 must set intra-file collision errors when flag is True"
        )
