"""Phase 16 tests: SCHD-driven un-ship of History jobs.

Covers §6 of Architecture/20260509-ProjectPhase16.md.
"""
from __future__ import annotations

from datetime import date
from unittest.mock import patch

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.models import (
    Assembly,
    BuildType,
    Customer,
    ImportBatch,
    ImportStagingRow,
    ImportStatus,
    Job,
    JobStatus,
    JobSupersessionCandidate,
    SheetKind,
)
from backend.app.sorting import resolve_ship_date
from backend.app.transform import _apply_shipped, _apply_unship, transform_staging_row


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _make_customer(session: Session, name: str = "ACME") -> Customer:
    c = Customer(name=name)
    session.add(c)
    session.flush()
    return c


def _make_assembly(session: Session, part_number: str = "137845") -> Assembly:
    a = Assembly(part_number=part_number)
    session.add(a)
    session.flush()
    return a


def _make_shipped_job(
    session: Session,
    assembly: Assembly,
    customer: Customer,
    shipped_at: date = date(2026, 4, 1),
    ship_date_text: str = "5/15",
) -> Job:
    """Create a job already in the shipped state with ever_shipped_at set."""
    job = Job(
        assembly_id=assembly.id,
        customer_id=customer.id,
        build_type=BuildType.new,
        quantity=10,
        status=JobStatus.shipped,
        shipped_at=shipped_at,
        ever_shipped_at=shipped_at,
        ship_date_text=ship_date_text,
    )
    session.add(job)
    session.flush()
    return job


def _make_live_batch(session: Session) -> ImportBatch:
    batch = ImportBatch(source_file="schd.xlsx", sheet_kind=SheetKind.live)
    session.add(batch)
    session.flush()
    return batch


def _make_historical_batch(session: Session) -> ImportBatch:
    batch = ImportBatch(source_file="aa.xlsx", sheet_kind=SheetKind.historical)
    session.add(batch)
    session.flush()
    return batch


def _seed_row(session: Session, batch: ImportBatch, **overrides) -> ImportStagingRow:
    defaults = dict(
        batch_id=batch.id,
        source_row_number=1,
        processing_status=ImportStatus.pending,
        raw_job="137845\nNEW",
        raw_qty="10",
        raw_customer="ACME",
        raw_ship_date="5/15",
    )
    defaults.update(overrides)
    row = ImportStagingRow(**defaults)
    session.add(row)
    session.flush()
    return row


# ---------------------------------------------------------------------------
# §6 test: _apply_shipped sets ever_shipped_at on first ship transition
# ---------------------------------------------------------------------------

class TestApplyShippedSetsEverShippedAt:
    def test_apply_shipped_sets_ever_shipped_at_first_time(self, session):
        """_apply_shipped sets ever_shipped_at when it is NULL (first ship)."""
        batch = _make_live_batch(session)
        customer = _make_customer(session)
        assembly = _make_assembly(session)
        job = Job(
            assembly_id=assembly.id,
            customer_id=customer.id,
            build_type=BuildType.new,
            quantity=5,
            status=JobStatus.planned,
        )
        session.add(job)
        session.flush()

        row = _seed_row(session, batch, raw_shipped="2026-05-01")

        result = _apply_shipped(row, job)

        assert result is True
        assert job.status is JobStatus.shipped
        assert job.shipped_at == date(2026, 5, 1)
        assert job.ever_shipped_at == date(2026, 5, 1)

    def test_apply_shipped_does_not_overwrite_ever_shipped_at_on_reship(self, session):
        """_apply_shipped preserves ever_shipped_at (INV-S3) on subsequent ship events."""
        batch = _make_live_batch(session)
        customer = _make_customer(session, "ACME2")
        assembly = _make_assembly(session, "900001")
        job = Job(
            assembly_id=assembly.id,
            customer_id=customer.id,
            build_type=BuildType.new,
            quantity=5,
            status=JobStatus.planned,
            shipped_at=None,
            ever_shipped_at=date(2026, 4, 1),  # previously shipped, now un-shipped
        )
        session.add(job)
        session.flush()

        row = _seed_row(session, batch, raw_shipped="2026-05-01")

        result = _apply_shipped(row, job)

        assert result is True
        assert job.shipped_at == date(2026, 5, 1)
        assert job.ever_shipped_at == date(2026, 4, 1)  # monotonic — unchanged


# ---------------------------------------------------------------------------
# §6 test: transform_staging_row un-ship path (F1)
# ---------------------------------------------------------------------------

class TestUnshipViaSCHD:
    def test_transform_unship_when_schd_blanks_shipped_field(self, session):
        """F1: SCHD row with blank raw_shipped un-ships an existing shipped job."""
        batch = _make_live_batch(session)
        customer = _make_customer(session)
        assembly = _make_assembly(session)
        job = _make_shipped_job(
            session, assembly, customer,
            shipped_at=date(2026, 4, 1),
            ship_date_text="5/15",
        )

        row = _seed_row(
            session, batch,
            raw_shipped="",
            raw_ship_date="5/15",
        )

        with patch("backend.app.transform._today", return_value=date(2026, 5, 9)):
            outcome = transform_staging_row(session, row, sheet_kind=SheetKind.live)

        assert outcome.action == "updated"
        session.expire_all()
        job = session.get(Job, job.id)
        assert job.status is JobStatus.planned
        assert job.shipped_at is None
        assert job.ever_shipped_at == date(2026, 4, 1)
        # resolve_ship_date for (planned, NULL shipped_at, "5/15", today=2026-05-09)
        assert job.resolved_ship_date == date(2026, 5, 15)
        assert row.processing_status is ImportStatus.processed

    def test_transform_overwrites_shipped_at_when_schd_supplies_new_date(self, session):
        """F2: SCHD row with new date overwrites shipped_at; ever_shipped_at is monotonic."""
        batch = _make_live_batch(session)
        customer = _make_customer(session, "ACME-F2")
        assembly = _make_assembly(session, "900002")
        job = _make_shipped_job(
            session, assembly, customer,
            shipped_at=date(2026, 4, 1),
        )

        row = _seed_row(
            session, batch,
            raw_job="900002\nNEW",
            raw_shipped="2026-05-09",
            raw_ship_date="5/15",
        )

        with patch("backend.app.transform._today", return_value=date(2026, 5, 9)):
            outcome = transform_staging_row(session, row, sheet_kind=SheetKind.live)

        assert outcome.action == "updated"
        session.expire_all()
        job = session.get(Job, job.id)
        assert job.status is JobStatus.shipped
        assert job.shipped_at == date(2026, 5, 9)
        assert job.ever_shipped_at == date(2026, 4, 1)  # monotonic

    def test_transform_historical_blank_does_not_unship(self, session):
        """F5: Historical (AA) ingest with blank raw_shipped produces conflict error."""
        batch = _make_historical_batch(session)
        customer = _make_customer(session, "ACME-F5")
        assembly = _make_assembly(session, "900003")
        original_shipped = date(2026, 4, 1)
        job = _make_shipped_job(
            session, assembly, customer,
            shipped_at=original_shipped,
        )

        row = _seed_row(
            session, batch,
            raw_job="900003\nNEW",
            raw_shipped="",
            raw_ship_date="5/15",
        )

        outcome = transform_staging_row(session, row, sheet_kind=SheetKind.historical)

        assert outcome.action == "errored"
        assert row.processing_status is ImportStatus.error
        assert "shipped" in row.processing_error.lower()
        session.expire_all()
        job = session.get(Job, job.id)
        assert job.status is JobStatus.shipped
        assert job.shipped_at == original_shipped
        assert job.ever_shipped_at == original_shipped


# ---------------------------------------------------------------------------
# §6 test: re-ingest of un-shipped row (F9)
# ---------------------------------------------------------------------------

class TestReIngestUnshippedJob:
    def test_reingest_schd_blank_for_already_unshipped_job(self, session):
        """F9: Re-ingesting SCHD with blank shipped for an already-planned job is a no-op."""
        batch = _make_live_batch(session)
        customer = _make_customer(session, "ACME-F9")
        assembly = _make_assembly(session, "900004")
        # Job already un-shipped: planned, ever_shipped_at set, shipped_at None.
        job = Job(
            assembly_id=assembly.id,
            customer_id=customer.id,
            build_type=BuildType.new,
            quantity=10,
            status=JobStatus.planned,
            shipped_at=None,
            ever_shipped_at=date(2026, 4, 1),
            ship_date_text="5/15",
        )
        session.add(job)
        session.flush()

        row = _seed_row(session, batch, raw_job="900004\nNEW", raw_shipped="", raw_ship_date="5/15")

        with patch("backend.app.transform._today", return_value=date(2026, 5, 9)):
            outcome = transform_staging_row(session, row, sheet_kind=SheetKind.live)

        assert outcome.action == "updated"
        session.expire_all()
        job = session.get(Job, job.id)
        assert job.status is JobStatus.planned
        assert job.shipped_at is None
        assert job.ever_shipped_at == date(2026, 4, 1)  # preserved


# ---------------------------------------------------------------------------
# §6 test: supersession shield after un-ship (F8 long-tail)
# ---------------------------------------------------------------------------

class TestSupersessionShieldAfterUnship:
    def test_supersession_shield_uses_ever_shipped_at_after_unship(
        self, schd_workbook_factory, workbook_factory, session_factory
    ):
        """An un-shipped job is shielded from supersession in future live batches.

        ever_shipped_at IS NOT NULL blocks candidate creation even when
        shipped_at IS NULL (planned status after un-ship).
        """
        from backend.app.ingest import ingest_workbook

        # Step 1: Ship a job via a historical workbook.
        hist_wb = workbook_factory(
            [{"JOB": "199001\nNEW", "QTY": "1", "CUSTOMER": "SHIELD-TEST",
              "SHIPPED": "04/01/2026"}]
        )
        ingest_workbook(hist_wb, session_factory=session_factory)

        with session_factory() as s:
            job = s.scalars(
                select(Job).join(Assembly).where(Assembly.part_number == "199001")
            ).one()
            assert job.status is JobStatus.shipped
            assert job.ever_shipped_at == date(2026, 4, 1)

        # Step 2: Un-ship via a SCHD workbook (blank raw_shipped).
        unship_wb = schd_workbook_factory(
            [{"data": {"JOB": "199001\nNEW", "QTY": "1", "CUSTOMER": "SHIELD-TEST",
                       "SHIPPED": "", "SHIP DATE": "5/15"}}],
            filename="schd_unship.xlsx",
        )
        ingest_workbook(unship_wb, session_factory=session_factory)

        with session_factory() as s:
            job = s.scalars(
                select(Job).join(Assembly).where(Assembly.part_number == "199001")
                .where(Job.superseded_at.is_(None))
                .where(Job.discarded_at.is_(None))
            ).one()
            assert job.status is JobStatus.planned
            assert job.shipped_at is None
            assert job.ever_shipped_at == date(2026, 4, 1)

        # Step 3: A subsequent live batch that does NOT reference 199001 bare.
        # The un-shipped job must not be opened as a candidate (shield fires).
        live_wb2 = schd_workbook_factory(
            [{"data": {"JOB": "199001-1par\nNEW", "QTY": "1", "CUSTOMER": "SHIELD-TEST"}}],
            filename="schd_omit.xlsx",
        )
        result = ingest_workbook(live_wb2, session_factory=session_factory)

        with session_factory() as s:
            cands = s.scalars(select(JobSupersessionCandidate)).all()
        assert cands == []

    def test_unship_then_disappear_does_not_open_candidate(
        self, schd_workbook_factory, workbook_factory, session_factory
    ):
        """End-to-end: un-ship then omit from next batch — no candidate opened."""
        from backend.app.ingest import ingest_workbook

        # Create the job via historical workbook.
        hist_wb = workbook_factory(
            [{"JOB": "199002\nNEW", "QTY": "2", "CUSTOMER": "DISAPPEAR-CO",
              "SHIPPED": "04/10/2026"}]
        )
        ingest_workbook(hist_wb, session_factory=session_factory)

        # Un-ship via SCHD.
        wb_unship = schd_workbook_factory(
            [{"data": {"JOB": "199002\nNEW", "QTY": "2", "CUSTOMER": "DISAPPEAR-CO",
                       "SHIPPED": ""}}],
            filename="unship_b1.xlsx",
        )
        result1 = ingest_workbook(wb_unship, session_factory=session_factory)

        # Second live batch — 199002 row absent entirely.
        wb_omit = schd_workbook_factory(
            [{"data": {"JOB": "199003\nNEW", "QTY": "1", "CUSTOMER": "DISAPPEAR-CO"}}],
            filename="omit_b2.xlsx",
        )
        result2 = ingest_workbook(wb_omit, session_factory=session_factory)

        # The un-shipped job's ever_shipped_at shields it from candidacy.
        with session_factory() as s:
            cands = s.scalars(
                select(JobSupersessionCandidate)
                .join(Job).join(Assembly)
                .where(Assembly.part_number == "199002")
            ).all()
        assert cands == []


# ---------------------------------------------------------------------------
# §6 test: resolve_ship_date handles post-unship input shape
# ---------------------------------------------------------------------------

class TestResolveShipDatePostUnship:
    def test_resolve_ship_date_planned_with_null_shipped_at(self):
        """resolve_ship_date handles (planned, NULL shipped_at) — the post-unship shape."""
        result = resolve_ship_date(
            ship_date_text="5/15",
            status=JobStatus.planned,
            shipped_at=None,
            today=date(2026, 5, 9),
        )
        assert result == date(2026, 5, 15)
