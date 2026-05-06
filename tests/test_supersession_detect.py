"""Candidate detection scenarios — split, recombine, nested-split, re-emerge.

Assertions are on (job_id, reason, detected_in_batch_id) tuples (stable
across schema changes — TDD assumptions) and on IngestResult counters.
"""
from __future__ import annotations

from sqlalchemy import select

from backend.app.ingest import ingest_workbook
from backend.app.models import (
    Assembly,
    CandidateReason,
    CandidateResolution,
    Job,
    JobSupersessionCandidate,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _pending_candidates(session_factory):
    """Return all pending JobSupersessionCandidate rows."""
    with session_factory() as s:
        return s.scalars(
            select(JobSupersessionCandidate)
            .where(JobSupersessionCandidate.resolved_at.is_(None))
        ).all()


def _job_id_for_suffix(session_factory, part_number, split_suffix):
    """Return the id of the active Job matching part_number + split_suffix."""
    with session_factory() as s:
        asm = s.scalars(
            select(Assembly).where(Assembly.part_number == part_number)
        ).one()
        return s.scalars(
            select(Job)
            .where(Job.assembly_id == asm.id)
            .where(Job.split_suffix == split_suffix)
            .where(Job.superseded_at.is_(None))
        ).one().id


# ---------------------------------------------------------------------------
# Split scenario: bare → split into two parts
# ---------------------------------------------------------------------------


def test_split_scenario_opens_one_candidate_for_bare_job(
    schd_workbook_factory, session_factory
):
    # live_v1 — bare 128764 NEW
    wb1 = schd_workbook_factory(
        [{"data": {"JOB": "128764\nNEW", "QTY": "1", "CUSTOMER": "ACME"}}]
    )
    r1 = ingest_workbook(wb1, session_factory=session_factory)
    assert r1.candidates_opened == 0
    assert r1.candidates_auto_returned == 0

    # live_v2 — split into -1par + -2par
    wb2 = schd_workbook_factory(
        [
            {"data": {"JOB": "128764-1par\nNEW", "QTY": "1", "CUSTOMER": "ACME"}},
            {"data": {"JOB": "128764-2par\nNEW", "QTY": "1", "CUSTOMER": "ACME"}},
        ]
    )
    r2 = ingest_workbook(wb2, session_factory=session_factory)
    assert r2.candidates_opened == 1
    assert r2.candidates_auto_returned == 0

    cands = _pending_candidates(session_factory)
    assert len(cands) == 1
    cand = cands[0]
    assert cand.reason is CandidateReason.orphan_after_split
    assert cand.detected_in_batch_id == r2.batch_id
    # The orphaned job is the bare 128764 (split_suffix IS NULL)
    with session_factory() as s:
        orphan_job = s.get(Job, cand.job_id)
    assert orphan_job.split_suffix is None


# ---------------------------------------------------------------------------
# Recombine scenario: two splits merge back to bare
# ---------------------------------------------------------------------------


def test_recombine_scenario_opens_candidates_for_each_split(
    schd_workbook_factory, session_factory
):
    # live_v1 — splits
    wb1 = schd_workbook_factory(
        [
            {"data": {"JOB": "129000-1par\nNEW", "QTY": "1", "CUSTOMER": "ACME"}},
            {"data": {"JOB": "129000-2par\nNEW", "QTY": "1", "CUSTOMER": "ACME"}},
        ]
    )
    r1 = ingest_workbook(wb1, session_factory=session_factory)
    assert r1.candidates_opened == 0

    # live_v2 — recombine
    wb2 = schd_workbook_factory(
        [{"data": {"JOB": "129000\nNEW", "QTY": "2", "CUSTOMER": "ACME"}}]
    )
    r2 = ingest_workbook(wb2, session_factory=session_factory)
    assert r2.candidates_opened == 2
    assert r2.candidates_auto_returned == 0

    cands = _pending_candidates(session_factory)
    assert len(cands) == 2
    reasons = {c.reason for c in cands}
    assert reasons == {CandidateReason.orphan_after_recombine}

    with session_factory() as s:
        suffixes = {s.get(Job, c.job_id).split_suffix for c in cands}
    assert suffixes == {"-1par", "-2par"}


# ---------------------------------------------------------------------------
# Nested-split scenario: -1par further splits into -1par-1bal + -1par-2bal
#
# The assembly for "130000-1par-Xbal" is "130000-1par" (different from
# "130000"). The "-1par" job lives on assembly "130000". Detection of "-1par"
# as an orphan requires that assembly "130000" is still touched in the new
# batch — achieved by keeping "-2par" present.
# ---------------------------------------------------------------------------


def test_nested_split_opens_candidate_for_intermediate_job(
    schd_workbook_factory, session_factory
):
    # live_v1 — both parts of assembly 130000 present
    wb1 = schd_workbook_factory(
        [
            {"data": {"JOB": "130000-1par\nNEW", "QTY": "1", "CUSTOMER": "ACME"}},
            {"data": {"JOB": "130000-2par\nNEW", "QTY": "1", "CUSTOMER": "ACME"}},
        ],
        filename="ns_v1.xlsx",
    )
    r1 = ingest_workbook(wb1, session_factory=session_factory)
    assert r1.candidates_opened == 0

    # live_v2 — -1par splits into sub-parts; -2par remains; -1par disappears
    # "-1par-1bal" → assembly "130000-1par", split_suffix "-1bal"
    # "-2par"      → assembly "130000",      split_suffix "-2par"  (still referenced)
    # "-1par" (assembly "130000", split_suffix "-1par") is orphaned
    wb2 = schd_workbook_factory(
        [
            {"data": {"JOB": "130000-1par-1bal\nNEW", "QTY": "1", "CUSTOMER": "ACME"}},
            {"data": {"JOB": "130000-1par-2bal\nNEW", "QTY": "1", "CUSTOMER": "ACME"}},
            {"data": {"JOB": "130000-2par\nNEW",      "QTY": "1", "CUSTOMER": "ACME"}},
        ],
        filename="ns_v2.xlsx",
    )
    r2 = ingest_workbook(wb2, session_factory=session_factory)
    assert r2.candidates_opened == 1

    cands = _pending_candidates(session_factory)
    assert len(cands) == 1
    # Reason: the orphaned -1par now has two sub-split siblings
    # in assembly "130000-1par", but from the outer-assembly perspective
    # (assembly "130000") only one referenced job remains (-2par, no split).
    # Actually the siblings comparison looks at the SAME assembly as the
    # orphan. The -2par job shares assembly "130000" but has split_suffix.
    # With only 1 sibling (non-empty split_suffix), this is ORPHAN_OTHER.
    with session_factory() as s:
        orphan_job = s.get(Job, cands[0].job_id)
    assert orphan_job.split_suffix == "-1par"


# ---------------------------------------------------------------------------
# Re-emerge scenario: a previously-orphaned job reappears
# ---------------------------------------------------------------------------


def test_re_emerge_auto_returns_prior_candidate(
    schd_workbook_factory, session_factory
):
    # live_v1 — bare
    wb1 = schd_workbook_factory(
        [{"data": {"JOB": "131000\nNEW", "QTY": "1", "CUSTOMER": "ACME"}}],
        filename="v1.xlsx",
    )
    ingest_workbook(wb1, session_factory=session_factory)

    # live_v2 — bare disappears; splits appear → pending candidate for bare
    wb2 = schd_workbook_factory(
        [
            {"data": {"JOB": "131000-1par\nNEW", "QTY": "1", "CUSTOMER": "ACME"}},
            {"data": {"JOB": "131000-2par\nNEW", "QTY": "1", "CUSTOMER": "ACME"}},
        ],
        filename="v2.xlsx",
    )
    r2 = ingest_workbook(wb2, session_factory=session_factory)
    assert r2.candidates_opened == 1

    # live_v3 — bare reappears; splits still present
    wb3 = schd_workbook_factory(
        [
            {"data": {"JOB": "131000\nNEW", "QTY": "1", "CUSTOMER": "ACME"}},
            {"data": {"JOB": "131000-1par\nNEW", "QTY": "1", "CUSTOMER": "ACME"}},
            {"data": {"JOB": "131000-2par\nNEW", "QTY": "1", "CUSTOMER": "ACME"}},
        ],
        filename="v3.xlsx",
    )
    r3 = ingest_workbook(wb3, session_factory=session_factory)
    assert r3.candidates_opened == 0
    assert r3.candidates_auto_returned == 1

    # No pending candidates remain
    assert _pending_candidates(session_factory) == []

    # The auto-returned candidate is in resolved state
    with session_factory() as s:
        cand = s.scalars(
            select(JobSupersessionCandidate)
            .where(JobSupersessionCandidate.detected_in_batch_id == r2.batch_id)
        ).one()
    assert cand.resolution is CandidateResolution.auto_returned
    assert cand.resolved_at is not None
