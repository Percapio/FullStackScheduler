"""Contract tests for POST /api/staging/supersession-candidates/{id}/reject.

Covers: happy path and already-resolved 409.
"""
from __future__ import annotations

from datetime import datetime, timezone

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


def _make_pending_candidate(session, part_suffix: str = "A") -> JobSupersessionCandidate:
    asm = Assembly(part_number=f"RJCT-{part_suffix}")
    session.add(asm)
    session.flush()
    cust = Customer(name=f"RJCT-CUST-{part_suffix}")
    session.add(cust)
    session.flush()
    batch = ImportBatch(
        source_file="test.xlsx",
        row_count=1,
        status=ImportStatus.processed,
        sheet_kind=SheetKind.live,
    )
    session.add(batch)
    session.flush()
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


class TestRejectCandidate:
    def test_reject_sets_resolution_reject(self, client, session):
        cand = _make_pending_candidate(session, "001")

        resp = client.post(f"/api/staging/supersession-candidates/{cand.id}/reject")
        assert resp.status_code == 200
        body = resp.json()
        assert body["id"] == cand.id
        assert body["resolution"] == CandidateResolution.reject.value
        assert body["resolved_at"] is not None

    def test_reject_does_not_mutate_job(self, client, session):
        cand = _make_pending_candidate(session, "002")
        job_id = cand.job_id

        client.post(f"/api/staging/supersession-candidates/{cand.id}/reject")

        session.expire_all()
        job = session.get(Job, job_id)
        assert job.superseded_at is None
        assert job.superseded_by_batch_id is None

    def test_unknown_candidate_is_404(self, client):
        resp = client.post("/api/staging/supersession-candidates/9999999/reject")
        assert resp.status_code == 404
        assert resp.json()["detail"]["code"] == "not_found"

    def test_already_resolved_is_409(self, client, session):
        cand = _make_pending_candidate(session, "003")
        cand.resolved_at = _utc()
        cand.resolution = CandidateResolution.approve
        session.flush()

        resp = client.post(f"/api/staging/supersession-candidates/{cand.id}/reject")
        assert resp.status_code == 409
        detail = resp.json()["detail"]
        assert detail["code"] == "already_closed"
        assert detail["current_resolution"] == CandidateResolution.approve.value
