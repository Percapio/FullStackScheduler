"""Contract tests for POST /api/staging/supersession-candidates/{id}/approve.

Covers: happy path (resolution=APPROVE), shield-trip (200 with REJECT),
and already-resolved 409.
"""
from __future__ import annotations

from datetime import date, datetime, timezone

from backend.app.models import (
    Assembly,
    CandidateReason,
    CandidateResolution,
    Customer,
    ImportBatch,
    ImportStatus,
    Job,
    JobSupersessionCandidate,
    SheetKind,
)


def _utc() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _make_assembly(session, part_number: str) -> Assembly:
    asm = Assembly(part_number=part_number)
    session.add(asm)
    session.flush()
    return asm


def _make_customer(session, name: str = "ACME") -> Customer:
    c = Customer(name=name)
    session.add(c)
    session.flush()
    return c


def _make_batch(session) -> ImportBatch:
    b = ImportBatch(
        source_file="test.xlsx",
        row_count=1,
        status=ImportStatus.processed,
        sheet_kind=SheetKind.live,
    )
    session.add(b)
    session.flush()
    return b


def _make_pending_candidate(session, part_suffix: str = "A") -> JobSupersessionCandidate:
    asm = _make_assembly(session, f"APRV-{part_suffix}")
    cust = _make_customer(session, f"CUST-{part_suffix}")
    batch = _make_batch(session)
    job = Job(assembly_id=asm.id, customer_id=cust.id, quantity=1)
    session.add(job)
    session.flush()
    cand = JobSupersessionCandidate(
        job_id=job.id,
        detected_in_batch_id=batch.id,
        reason=CandidateReason.orphan_other,
        detected_at=_utc(),
    )
    session.add(cand)
    session.flush()
    return cand


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


class TestApproveCandidate:
    def test_approve_sets_resolution_approve(self, client, session):
        cand = _make_pending_candidate(session, "001")

        resp = client.post(f"/api/staging/supersession-candidates/{cand.id}/approve")
        assert resp.status_code == 200
        body = resp.json()
        assert body["id"] == cand.id
        assert body["resolution"] == CandidateResolution.approve.value
        assert body["resolved_at"] is not None

    def test_approve_supersedes_job(self, client, session):
        cand = _make_pending_candidate(session, "002")
        job_id = cand.job_id

        client.post(f"/api/staging/supersession-candidates/{cand.id}/approve")

        session.expire_all()
        job = session.get(Job, job_id)
        assert job.superseded_at is not None
        assert job.superseded_by_batch_id == cand.detected_in_batch_id

    def test_unknown_candidate_is_404(self, client):
        resp = client.post("/api/staging/supersession-candidates/9999999/approve")
        assert resp.status_code == 404
        assert resp.json()["detail"]["code"] == "not_found"

    def test_already_resolved_is_409(self, client, session):
        cand = _make_pending_candidate(session, "003")
        # Resolve it first.
        cand.resolved_at = _utc()
        cand.resolution = CandidateResolution.reject
        session.flush()

        resp = client.post(f"/api/staging/supersession-candidates/{cand.id}/approve")
        assert resp.status_code == 409
        detail = resp.json()["detail"]
        assert detail["code"] == "already_closed"
        assert detail["current_resolution"] == CandidateResolution.reject.value


# ---------------------------------------------------------------------------
# Shield trip
# ---------------------------------------------------------------------------


class TestApproveShieldTrip:
    def test_shipped_job_approval_returns_200_with_reject_resolution(
        self, client, session
    ):
        asm = _make_assembly(session, "SHIELD-001")
        cust = _make_customer(session, "SHIELD-CO")
        batch = _make_batch(session)
        # Job has shipped — shield should fire.
        job = Job(
            assembly_id=asm.id,
            customer_id=cust.id,
            quantity=1,
            shipped_at=date(2026, 1, 1),
        )
        session.add(job)
        session.flush()
        cand = JobSupersessionCandidate(
            job_id=job.id,
            detected_in_batch_id=batch.id,
            reason=CandidateReason.orphan_other,
            detected_at=_utc(),
        )
        session.add(cand)
        session.flush()

        resp = client.post(f"/api/staging/supersession-candidates/{cand.id}/approve")
        # Shield trip returns 200 (not 4xx) — operator sees the outcome.
        assert resp.status_code == 200
        body = resp.json()
        assert body["resolution"] == CandidateResolution.reject.value
        assert body["closed_by_shield_reason"] == "shipped_at_set"

    def test_shield_trip_does_not_mutate_job(self, client, session):
        asm = _make_assembly(session, "SHIELD-002")
        cust = _make_customer(session, "SHIELD-CO2")
        batch = _make_batch(session)
        job = Job(
            assembly_id=asm.id,
            customer_id=cust.id,
            quantity=1,
            shipped_at=date(2026, 2, 1),
        )
        session.add(job)
        session.flush()
        cand = JobSupersessionCandidate(
            job_id=job.id,
            detected_in_batch_id=batch.id,
            reason=CandidateReason.orphan_other,
            detected_at=_utc(),
        )
        session.add(cand)
        session.flush()

        client.post(f"/api/staging/supersession-candidates/{cand.id}/approve")

        session.expire_all()
        job = session.get(Job, job.id)
        assert job.superseded_at is None
        assert job.superseded_by_batch_id is None
