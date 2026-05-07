"""Backend tests for Phase 15 Epoch 3: Job soft-delete (discarded_at).

Tests per TDD §5.9:
  - test_jobs_discard: happy path sets discarded_at; already-discarded returns 409
    with the existing row in the body (Audit-01 #6); shipped-status job returns 409;
    unknown jobId returns 404; discarded job vanishes from /api/jobs/shipping;
    lineage still surfaces it for audit.
  - test_jobs_discard_then_reingest_same_identity: Audit-01 #1's acceptance check.
    After discarding job J at identity I, an ingest pipeline run with a row
    resolving to identity I must create a new active job J' at identity I; J stays
    discarded; no UNIQUE violation surfaces.
"""
from __future__ import annotations

from datetime import UTC, datetime, date

import pytest
from fastapi import status

from backend.app.models import (
    Assembly,
    BuildType,
    Customer,
    ImportBatch,
    ImportStagingRow,
    ImportStatus,
    Job,
    JobStatus,
)
from backend.app.services.jobs import discard_job, get_job_including_discarded, JobDiscardError


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_job(session, *, part_number="DISC-001", customer_name="DiscardCo", **overrides) -> Job:
    assembly = session.execute(
        __import__("sqlalchemy", fromlist=["select"]).select(Assembly).where(Assembly.part_number == part_number)
    ).scalar_one_or_none()
    if assembly is None:
        assembly = Assembly(part_number=part_number)
        session.add(assembly)
        session.flush()

    customer = session.execute(
        __import__("sqlalchemy", fromlist=["select"]).select(Customer).where(Customer.name == customer_name)
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


def _make_staging_row(session, batch, *, raw_job: str, raw_qty: str = "5",
                      raw_customer: str = "ReingestCo",
                      source_row_number: int = 1, **overrides) -> ImportStagingRow:
    row = ImportStagingRow(
        batch_id=batch.id,
        source_row_number=source_row_number,
        processing_status=ImportStatus.pending,
        raw_job=raw_job,
        raw_qty=raw_qty,
        raw_customer=raw_customer,
        **overrides,
    )
    session.add(row)
    session.flush()
    return row


# ---------------------------------------------------------------------------
# Service-layer unit tests
# ---------------------------------------------------------------------------

class TestDiscardJobService:
    def test_discard_planned_job_sets_discarded_at(self, session):
        job = _make_job(session, status=JobStatus.planned)
        assert job.discarded_at is None

        returned = discard_job(session, job.id)

        assert returned.id == job.id
        assert returned.discarded_at is not None

    def test_discard_wip_job_sets_discarded_at(self, session):
        job = _make_job(session, status=JobStatus.wip)
        returned = discard_job(session, job.id)
        assert returned.discarded_at is not None

    def test_discard_shipped_job_raises_conflict(self, session):
        job = _make_job(session, status=JobStatus.shipped,
                        shipped_at=date(2026, 1, 1))
        with pytest.raises(JobDiscardError, match="cannot discard shipped"):
            discard_job(session, job.id)

    def test_discard_already_discarded_raises_conflict(self, session):
        job = _make_job(session)
        job.discarded_at = datetime(2026, 1, 1)
        session.flush()

        with pytest.raises(JobDiscardError, match="already discarded"):
            discard_job(session, job.id)

    def test_discard_unknown_id_raises_not_found(self, session):
        with pytest.raises(JobDiscardError, match="not found"):
            discard_job(session, 999_999)

    def test_discarded_job_not_returned_by_active_query(self, session):
        from sqlalchemy import select
        from backend.app.services.jobs import _active_jobs_base

        job = _make_job(session)
        job.discarded_at = datetime.now(UTC).replace(tzinfo=None)
        session.flush()

        active_ids = [
            r.id for r in session.scalars(_active_jobs_base()).all()
        ]
        assert job.id not in active_ids


# ---------------------------------------------------------------------------
# API endpoint tests
# ---------------------------------------------------------------------------

class TestDiscardJobEndpoint:
    def test_discard_happy_path_returns_job(self, client, session):
        job = _make_job(session)
        session.commit()

        resp = client.post(f"/api/jobs/{job.id}/discard")
        assert resp.status_code == status.HTTP_200_OK

        body = resp.json()
        assert body["id"] == job.id
        assert body["discarded_at"] is not None

    def test_discard_job_vanishes_from_shipping(self, client, session):
        job = _make_job(session, part_number="SHIP-DISC-001", status=JobStatus.planned)
        session.commit()

        # Confirm the job is initially present in shipping.
        shipping_before = client.get("/api/jobs/shipping").json()
        ids_before = [j["id"] for j in shipping_before]
        assert job.id in ids_before

        client.post(f"/api/jobs/{job.id}/discard")

        shipping_after = client.get("/api/jobs/shipping").json()
        ids_after = [j["id"] for j in shipping_after]
        assert job.id not in ids_after

    def test_discard_already_discarded_returns_409_with_body(self, client, session):
        """Audit-01 #6: already-discarded should 409, not 404, and return the
        existing row so a polling client can converge."""
        job = _make_job(session)
        job.discarded_at = datetime(2026, 1, 1)
        session.commit()

        resp = client.post(f"/api/jobs/{job.id}/discard")
        assert resp.status_code == status.HTTP_409_CONFLICT

        body = resp.json()["detail"]
        # Body must include the existing job row (not just a string).
        assert isinstance(body, dict)
        assert body["job"]["id"] == job.id
        assert body["job"]["discarded_at"] is not None

    def test_discard_shipped_job_returns_409(self, client, session):
        job = _make_job(session, status=JobStatus.shipped, shipped_at=date(2026, 1, 1))
        session.commit()

        resp = client.post(f"/api/jobs/{job.id}/discard")
        assert resp.status_code == status.HTTP_409_CONFLICT
        assert "shipped" in resp.json()["detail"].lower()

    def test_discard_unknown_job_returns_404(self, client):
        resp = client.post("/api/jobs/999999/discard")
        assert resp.status_code == status.HTTP_404_NOT_FOUND

    def test_lineage_surfaces_discarded_job(self, client, session):
        """Discarded jobs must remain in lineage for audit (TDD §5.2)."""
        job = _make_job(session, part_number="LINEAGE-DISC-001")
        session.commit()

        job_id = job.id
        client.post(f"/api/jobs/{job_id}/discard")

        lineage = client.get(f"/api/jobs/{job_id}/lineage").json()
        lineage_ids = [j["id"] for j in lineage]
        assert job_id in lineage_ids

    def test_discarded_job_not_in_history(self, client, session):
        """Discarded jobs must not appear in history (TDD §5.2).

        Shipped jobs cannot be discarded via the API (409), so we verify the
        DB-level filter by manually setting discarded_at — which represents the
        defence-in-depth path (e.g. manual data correction or a future policy change).
        """
        job = _make_job(
            session,
            part_number="HIST-DISC-001",
            status=JobStatus.shipped,
            shipped_at=date(2026, 1, 1),
        )
        session.commit()

        history_before = [j["id"] for j in client.get("/api/jobs/history").json()]
        assert job.id in history_before

        # Bypass the service guard and set discarded_at directly (DB-level filter test).
        job.discarded_at = datetime(2026, 1, 2)
        session.commit()

        history_after = [j["id"] for j in client.get("/api/jobs/history").json()]
        assert job.id not in history_after


# ---------------------------------------------------------------------------
# Reingest acceptance check (Audit-01 #1)
# ---------------------------------------------------------------------------

class TestDiscardThenReingestSameIdentity:
    def test_reingest_after_discard_creates_new_job_no_unique_violation(
        self, client, session, open_batch
    ):
        """After discarding job J at identity I, an ingest pipeline run with a row
        resolving to the same identity I must create a new active job J', while J
        remains discarded, and no UNIQUE constraint violation surfaces.

        This is Audit-01 #1's acceptance check for the ix_job_identity_active
        partial-index predicate amendment in §5.2.
        """
        from backend.app.transform import transform_staging_row

        # Seed the assembly and customer so the row will parse correctly.
        raw_job = "TEST-REINGEST NEW"
        row1 = _make_staging_row(
            session, open_batch,
            raw_job=raw_job,
            raw_qty="5",
            raw_customer="ReingestCo",
        )
        # First ingest: create job J.
        transform_staging_row(session, row1)
        session.flush()

        assert row1.processing_status == ImportStatus.processed
        j_id = row1.resolved_job_id
        assert j_id is not None

        # Discard job J.
        j = session.get(Job, j_id)
        j.discarded_at = datetime.now(UTC).replace(tzinfo=None)
        session.flush()

        # Second ingest: same identity should create a new active job J'.
        batch2 = ImportBatch(source_file="reingest.xlsx")
        session.add(batch2)
        session.flush()

        row2 = _make_staging_row(
            session, batch2,
            raw_job=raw_job,
            raw_qty="10",
            raw_customer="ReingestCo",
            source_row_number=2,
        )

        # Must not raise any IntegrityError / UNIQUE constraint violation.
        transform_staging_row(session, row2)
        session.flush()

        assert row2.processing_status == ImportStatus.processed, \
            f"Expected processed, got {row2.processing_status}: {row2.processing_error}"
        j_prime_id = row2.resolved_job_id
        assert j_prime_id is not None
        assert j_prime_id != j_id, "Reingest must create a new Job, not reuse the discarded one"

        # Original job J stays discarded.
        session.expire(j)
        j_reloaded = session.get(Job, j_id)
        assert j_reloaded.discarded_at is not None

        # J' is active (discarded_at IS NULL, superseded_at IS NULL).
        j_prime = session.get(Job, j_prime_id)
        assert j_prime.discarded_at is None
        assert j_prime.superseded_at is None
