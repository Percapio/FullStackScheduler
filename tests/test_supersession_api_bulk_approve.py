"""Contract tests for POST /api/staging/supersession-candidates/bulk-approve.

Covers: all-approve happy path, partial-shield case (one shielded → four
approve), already-closed and not-found handling, and empty/missing ids 422.
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


def _make_batch(session) -> ImportBatch:
    b = ImportBatch(
        source_file="bulk.xlsx",
        row_count=1,
        status=ImportStatus.processed,
        sheet_kind=SheetKind.live,
    )
    session.add(b)
    session.flush()
    return b


def _make_candidate(session, batch, part_suffix: str, *, shipped: bool = False) -> JobSupersessionCandidate:
    asm = Assembly(part_number=f"BULK-{part_suffix}")
    session.add(asm)
    session.flush()
    cust = Customer(name=f"BULK-CUST-{part_suffix}")
    session.add(cust)
    session.flush()
    job = Job(
        assembly_id=asm.id,
        customer_id=cust.id,
        quantity=1,
        shipped_at=date(2026, 1, 1) if shipped else None,
        ever_shipped_at=date(2026, 1, 1) if shipped else None,
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
    return cand


class TestBulkApprove:
    def test_all_pending_approved(self, client, session):
        batch = _make_batch(session)
        cands = [_make_candidate(session, batch, f"OK{i}") for i in range(3)]
        ids = [c.id for c in cands]

        resp = client.post(
            "/api/staging/supersession-candidates/bulk-approve",
            json={"ids": ids},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert set(body["approved"]) == set(ids)
        assert body["shield_rejected"] == []
        assert body["already_closed"] == []
        assert body["not_found"] == []

    def test_partial_shield_case(self, client, session):
        """4 eligible + 1 shipped → 4 approved, 1 shield_rejected."""
        batch = _make_batch(session)
        ok_cands = [_make_candidate(session, batch, f"PRTL{i}") for i in range(4)]
        shielded = _make_candidate(session, batch, "PRTL-SHIP", shipped=True)
        ids = [c.id for c in ok_cands] + [shielded.id]

        resp = client.post(
            "/api/staging/supersession-candidates/bulk-approve",
            json={"ids": ids},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert set(body["approved"]) == {c.id for c in ok_cands}
        assert body["shield_rejected"] == [shielded.id]
        assert body["already_closed"] == []
        assert body["not_found"] == []

        # Verify ok_cands jobs are actually superseded.
        session.expire_all()
        for c in ok_cands:
            job = session.get(Job, c.job_id)
            assert job.superseded_at is not None

    def test_already_closed_candidate_goes_into_already_closed(self, client, session):
        batch = _make_batch(session)
        closed_cand = _make_candidate(session, batch, "AC01")
        closed_cand.resolved_at = _utc()
        closed_cand.resolution = CandidateResolution.reject
        session.flush()
        open_cand = _make_candidate(session, batch, "AC02")

        resp = client.post(
            "/api/staging/supersession-candidates/bulk-approve",
            json={"ids": [closed_cand.id, open_cand.id]},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["already_closed"] == [closed_cand.id]
        assert body["approved"] == [open_cand.id]

    def test_not_found_id_goes_into_not_found(self, client, session):
        batch = _make_batch(session)
        cand = _make_candidate(session, batch, "NF01")

        resp = client.post(
            "/api/staging/supersession-candidates/bulk-approve",
            json={"ids": [9999999, cand.id]},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["not_found"] == [9999999]
        assert body["approved"] == [cand.id]

    def test_empty_ids_list_is_422(self, client):
        resp = client.post(
            "/api/staging/supersession-candidates/bulk-approve",
            json={"ids": []},
        )
        assert resp.status_code == 422

    def test_missing_ids_field_is_422(self, client):
        resp = client.post(
            "/api/staging/supersession-candidates/bulk-approve",
            json={},
        )
        assert resp.status_code == 422

    def test_duplicate_ids_are_deduplicated_server_side(self, client, session):
        """Sending the same id twice should produce a single approval."""
        batch = _make_batch(session)
        cand = _make_candidate(session, batch, "DUP01")

        resp = client.post(
            "/api/staging/supersession-candidates/bulk-approve",
            json={"ids": [cand.id, cand.id]},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["approved"] == [cand.id]
        assert body["already_closed"] == []
