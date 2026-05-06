"""--force re-ingest semantics (audit #9) — four cases.

Each case is documented in TDD §Epoch 3 and pinned here so future
refactors cannot silently change the policy.
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select

from backend.app.ingest import ingest_workbook
from backend.app.models import (
    CandidateResolution,
    Job,
    JobSupersessionCandidate,
)


# ---------------------------------------------------------------------------
# Case 1: pending candidate — force re-ingest skips minting a duplicate
# ---------------------------------------------------------------------------


def test_force_reingest_pending_candidate_zero_new(schd_workbook_factory, session_factory):
    wb1 = schd_workbook_factory(
        [{"data": {"JOB": "150000\nNEW", "QTY": "1", "CUSTOMER": "ACME"}}],
        filename="fr_v1a.xlsx",
    )
    ingest_workbook(wb1, session_factory=session_factory)

    wb2 = schd_workbook_factory(
        [
            {"data": {"JOB": "150000-1par\nNEW", "QTY": "1", "CUSTOMER": "ACME"}},
            {"data": {"JOB": "150000-2par\nNEW", "QTY": "1", "CUSTOMER": "ACME"}},
        ],
        filename="fr_v2a.xlsx",
    )
    ingest_workbook(wb2, session_factory=session_factory)

    # Pending candidate exists. Force re-ingest of v2 — must be no-op.
    r = ingest_workbook(wb2, force=True, session_factory=session_factory)
    assert r.candidates_opened == 0

    with session_factory() as s:
        cands = s.scalars(select(JobSupersessionCandidate)).all()
    assert len(cands) == 1
    assert cands[0].resolved_at is None


# ---------------------------------------------------------------------------
# Case 2: auto-returned candidate — force re-ingest mints a fresh one
# ---------------------------------------------------------------------------


def test_force_reingest_after_auto_return_mints_new_candidate(
    schd_workbook_factory, session_factory
):
    wb1 = schd_workbook_factory(
        [{"data": {"JOB": "151000\nNEW", "QTY": "1", "CUSTOMER": "ACME"}}],
        filename="fr2_v1.xlsx",
    )
    ingest_workbook(wb1, session_factory=session_factory)

    wb2 = schd_workbook_factory(
        [
            {"data": {"JOB": "151000-1par\nNEW", "QTY": "1", "CUSTOMER": "ACME"}},
            {"data": {"JOB": "151000-2par\nNEW", "QTY": "1", "CUSTOMER": "ACME"}},
        ],
        filename="fr2_v2.xlsx",
    )
    ingest_workbook(wb2, session_factory=session_factory)

    # Bare job re-appears → candidate auto-returned
    wb3 = schd_workbook_factory(
        [
            {"data": {"JOB": "151000\nNEW", "QTY": "1", "CUSTOMER": "ACME"}},
            {"data": {"JOB": "151000-1par\nNEW", "QTY": "1", "CUSTOMER": "ACME"}},
            {"data": {"JOB": "151000-2par\nNEW", "QTY": "1", "CUSTOMER": "ACME"}},
        ],
        filename="fr2_v3.xlsx",
    )
    r3 = ingest_workbook(wb3, session_factory=session_factory)
    assert r3.candidates_auto_returned == 1

    # Force re-ingest v2 (bare absent again) — fresh candidate must appear
    r_force = ingest_workbook(wb2, force=True, session_factory=session_factory)
    assert r_force.candidates_opened == 1

    with session_factory() as s:
        pending = s.scalars(
            select(JobSupersessionCandidate)
            .where(JobSupersessionCandidate.resolved_at.is_(None))
        ).all()
    assert len(pending) == 1


# ---------------------------------------------------------------------------
# Case 3: approved candidate (job superseded) — force re-ingest is a no-op
# ---------------------------------------------------------------------------


def test_force_reingest_after_approval_zero_new_candidates(
    schd_workbook_factory, session_factory
):
    wb1 = schd_workbook_factory(
        [{"data": {"JOB": "152000\nNEW", "QTY": "1", "CUSTOMER": "ACME"}}],
        filename="fr3_v1.xlsx",
    )
    ingest_workbook(wb1, session_factory=session_factory)

    wb2 = schd_workbook_factory(
        [
            {"data": {"JOB": "152000-1par\nNEW", "QTY": "1", "CUSTOMER": "ACME"}},
            {"data": {"JOB": "152000-2par\nNEW", "QTY": "1", "CUSTOMER": "ACME"}},
        ],
        filename="fr3_v2.xlsx",
    )
    r2 = ingest_workbook(wb2, session_factory=session_factory)
    assert r2.candidates_opened == 1

    # Manually approve: supersede the bare job (simulates Epoch 4 approval)
    with session_factory() as s:
        cand = s.scalars(
            select(JobSupersessionCandidate)
            .where(JobSupersessionCandidate.resolved_at.is_(None))
        ).one()
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        cand.resolved_at = now
        cand.resolution = CandidateResolution.approve
        job = s.get(Job, cand.job_id)
        job.superseded_at = now
        job.superseded_by_batch_id = cand.detected_in_batch_id
        s.commit()

    # Force re-ingest v2 — superseded job is excluded by superseded_at IS NULL filter
    r_force = ingest_workbook(wb2, force=True, session_factory=session_factory)
    assert r_force.candidates_opened == 0

    with session_factory() as s:
        pending = s.scalars(
            select(JobSupersessionCandidate)
            .where(JobSupersessionCandidate.resolved_at.is_(None))
        ).all()
    assert len(pending) == 0


# ---------------------------------------------------------------------------
# Case 4: rejected candidate — force re-ingest mints a fresh one
# ---------------------------------------------------------------------------


def test_force_reingest_after_rejection_mints_new_candidate(
    schd_workbook_factory, session_factory
):
    wb1 = schd_workbook_factory(
        [{"data": {"JOB": "153000\nNEW", "QTY": "1", "CUSTOMER": "ACME"}}],
        filename="fr4_v1.xlsx",
    )
    ingest_workbook(wb1, session_factory=session_factory)

    wb2 = schd_workbook_factory(
        [
            {"data": {"JOB": "153000-1par\nNEW", "QTY": "1", "CUSTOMER": "ACME"}},
            {"data": {"JOB": "153000-2par\nNEW", "QTY": "1", "CUSTOMER": "ACME"}},
        ],
        filename="fr4_v2.xlsx",
    )
    r2 = ingest_workbook(wb2, session_factory=session_factory)
    assert r2.candidates_opened == 1

    # Manually reject the candidate (simulates Epoch 4 rejection)
    with session_factory() as s:
        cand = s.scalars(
            select(JobSupersessionCandidate)
            .where(JobSupersessionCandidate.resolved_at.is_(None))
        ).one()
        cand.resolved_at = datetime.now(timezone.utc).replace(tzinfo=None)
        cand.resolution = CandidateResolution.reject
        s.commit()

    # Force re-ingest v2 — job is still active (not superseded) → new candidate
    r_force = ingest_workbook(wb2, force=True, session_factory=session_factory)
    assert r_force.candidates_opened == 1

    with session_factory() as s:
        pending = s.scalars(
            select(JobSupersessionCandidate)
            .where(JobSupersessionCandidate.resolved_at.is_(None))
        ).all()
    assert len(pending) == 1
