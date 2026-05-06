"""Shield at detection time — shipped jobs are never opened as candidates.

Jobs whose shipped_at IS NOT NULL must be excluded even when they
disappear from a live workbook.
"""
from __future__ import annotations

from datetime import date

from sqlalchemy import select

from backend.app.ingest import ingest_workbook
from backend.app.models import Assembly, Job, JobStatus, JobSupersessionCandidate


def test_shipped_job_is_not_opened_as_candidate(
    schd_workbook_factory, workbook_factory, session_factory
):
    """A job that has shipped must never become a supersession candidate."""
    # Ingest a SHIPPED (AA) workbook to create a shipped job.
    hist_wb = workbook_factory(
        [{"JOB": "160000\nNEW", "QTY": "2", "CUSTOMER": "SHIELD-CO",
          "SHIPPED": "03/10/2026"}]
    )
    ingest_workbook(hist_wb, session_factory=session_factory)

    with session_factory() as s:
        asm = s.scalars(
            select(Assembly).where(Assembly.part_number == "160000")
        ).one()
        shipped_job = s.scalars(
            select(Job).where(Job.assembly_id == asm.id)
        ).one()
        assert shipped_job.shipped_at == date(2026, 3, 10)

    # Ingest a live workbook for the same assembly that does NOT reference
    # 160000 bare — only references a split variant.
    live_wb = schd_workbook_factory(
        [
            {"data": {"JOB": "160000-1par\nNEW", "QTY": "1", "CUSTOMER": "SHIELD-CO"}},
            {"data": {"JOB": "160000-2par\nNEW", "QTY": "1", "CUSTOMER": "SHIELD-CO"}},
        ]
    )
    result = ingest_workbook(live_wb, session_factory=session_factory)

    # The shipped bare job must not be opened as a candidate.
    assert result.candidates_opened == 0

    with session_factory() as s:
        all_cands = s.scalars(select(JobSupersessionCandidate)).all()
    assert all_cands == []


def test_unshipped_job_is_opened_as_candidate_in_same_assembly(
    schd_workbook_factory, session_factory
):
    """Baseline: an unshipped active job IS opened as a candidate."""
    wb1 = schd_workbook_factory(
        [{"data": {"JOB": "161000\nNEW", "QTY": "1", "CUSTOMER": "SHIELD-CO"}}],
        filename="shield2_v1.xlsx",
    )
    ingest_workbook(wb1, session_factory=session_factory)

    wb2 = schd_workbook_factory(
        [
            {"data": {"JOB": "161000-1par\nNEW", "QTY": "1", "CUSTOMER": "SHIELD-CO"}},
            {"data": {"JOB": "161000-2par\nNEW", "QTY": "1", "CUSTOMER": "SHIELD-CO"}},
        ],
        filename="shield2_v2.xlsx",
    )
    result = ingest_workbook(wb2, session_factory=session_factory)
    assert result.candidates_opened == 1
