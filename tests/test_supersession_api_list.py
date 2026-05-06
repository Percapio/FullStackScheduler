"""Contract tests for GET /api/staging/supersession-candidates.

Covers: pagination, status filter (pending/resolved/all),
resolution filter combinations, and 422 validation.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

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


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _utc() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _make_assembly(session, part_number: str) -> Assembly:
    asm = Assembly(part_number=part_number)
    session.add(asm)
    session.flush()
    return asm


def _make_customer(session, name: str = "ACME") -> Customer:
    existing = session.get(Customer, 1)
    if existing and existing.name == name:
        return existing
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


def _make_job(session, assembly_id: int, customer_id: int) -> Job:
    j = Job(assembly_id=assembly_id, customer_id=customer_id, quantity=1)
    session.add(j)
    session.flush()
    return j


def _make_candidate(
    session,
    job: Job,
    batch: ImportBatch,
    *,
    resolved_at: datetime | None = None,
    resolution: CandidateResolution | None = None,
    reason: CandidateReason = CandidateReason.orphan_other,
) -> JobSupersessionCandidate:
    cand = JobSupersessionCandidate(
        job_id=job.id,
        detected_in_batch_id=batch.id,
        reason=reason,
        detected_at=_utc(),
        resolved_at=resolved_at,
        resolution=resolution,
    )
    session.add(cand)
    session.flush()
    return cand


# ---------------------------------------------------------------------------
# Status filter
# ---------------------------------------------------------------------------


class TestListCandidatesStatusFilter:
    def test_default_returns_only_pending(self, client, session):
        asm = _make_assembly(session, "LIST-001")
        cust = _make_customer(session)
        batch = _make_batch(session)
        j1 = _make_job(session, asm.id, cust.id)
        j2 = _make_job(session, asm.id, cust.id)
        pending = _make_candidate(session, j1, batch)
        _make_candidate(
            session, j2, batch,
            resolved_at=_utc(),
            resolution=CandidateResolution.reject,
        )

        resp = client.get("/api/staging/supersession-candidates")
        assert resp.status_code == 200
        body = resp.json()
        ids = {item["id"] for item in body["items"]}
        assert pending.id in ids
        assert body["total"] == 1

    def test_status_resolved_returns_only_resolved(self, client, session):
        asm = _make_assembly(session, "LIST-002")
        cust = _make_customer(session)
        batch = _make_batch(session)
        j1 = _make_job(session, asm.id, cust.id)
        j2 = _make_job(session, asm.id, cust.id)
        _make_candidate(session, j1, batch)
        resolved = _make_candidate(
            session, j2, batch,
            resolved_at=_utc(),
            resolution=CandidateResolution.approve,
        )

        resp = client.get("/api/staging/supersession-candidates?status=resolved")
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == 1
        assert body["items"][0]["id"] == resolved.id

    def test_status_all_returns_every_candidate(self, client, session):
        asm = _make_assembly(session, "LIST-003")
        cust = _make_customer(session)
        batch = _make_batch(session)
        j1 = _make_job(session, asm.id, cust.id)
        j2 = _make_job(session, asm.id, cust.id)
        _make_candidate(session, j1, batch)
        _make_candidate(
            session, j2, batch,
            resolved_at=_utc(),
            resolution=CandidateResolution.reject,
        )

        resp = client.get("/api/staging/supersession-candidates?status=all")
        assert resp.status_code == 200
        assert resp.json()["total"] == 2

    def test_invalid_status_is_422(self, client):
        resp = client.get("/api/staging/supersession-candidates?status=unknown")
        assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Resolution filter
# ---------------------------------------------------------------------------


class TestListCandidatesResolutionFilter:
    def test_resolved_with_resolution_approve_filters_correctly(
        self, client, session
    ):
        asm = _make_assembly(session, "RESFILT-001")
        cust = _make_customer(session)
        batch = _make_batch(session)
        j1 = _make_job(session, asm.id, cust.id)
        j2 = _make_job(session, asm.id, cust.id)
        approved = _make_candidate(
            session, j1, batch,
            resolved_at=_utc(),
            resolution=CandidateResolution.approve,
        )
        _make_candidate(
            session, j2, batch,
            resolved_at=_utc(),
            resolution=CandidateResolution.reject,
        )

        resp = client.get(
            "/api/staging/supersession-candidates?status=resolved&resolution=approve"
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == 1
        assert body["items"][0]["id"] == approved.id

    def test_pending_with_resolution_param_is_422(self, client):
        resp = client.get(
            "/api/staging/supersession-candidates?status=pending&resolution=approve"
        )
        assert resp.status_code == 422

    def test_invalid_resolution_is_422(self, client):
        resp = client.get(
            "/api/staging/supersession-candidates?status=resolved&resolution=bogus"
        )
        assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Pagination
# ---------------------------------------------------------------------------


class TestListCandidatesPagination:
    def test_limit_and_offset_work(self, client, session):
        asm = _make_assembly(session, "PAGE-001")
        cust = _make_customer(session)
        batch = _make_batch(session)
        for _ in range(5):
            j = _make_job(session, asm.id, cust.id)
            _make_candidate(session, j, batch)

        resp = client.get(
            "/api/staging/supersession-candidates?status=all&limit=2&offset=1"
        )
        assert resp.status_code == 200
        body = resp.json()
        assert len(body["items"]) == 2
        assert body["total"] == 5
