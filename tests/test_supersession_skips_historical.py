"""Historical batches produce zero candidates regardless of database state.

A SHIPPED (AA) ingest must never open supersession candidates, even when
active jobs are present in the same assemblies.
"""
from __future__ import annotations

from sqlalchemy import select

from backend.app.ingest import ingest_workbook
from backend.app.models import Assembly, JobSupersessionCandidate, SheetKind


def test_historical_ingest_writes_no_candidates(workbook_factory, session_factory):
    """Pure historical ingest: no prior jobs, no candidates expected."""
    # Phase 18b: pre-seed assembly so Stage 3.5 treats it as known and
    # Stage 4..6 runs inline (no review hold).
    with session_factory() as s:
        s.add(Assembly(part_number="9999A"))
        s.commit()

    path = workbook_factory(
        [{"JOB": "9999A\nNEW", "QTY": "5", "CUSTOMER": "HIST-CO",
          "SHIPPED": "08/15/2025"}]
    )
    result = ingest_workbook(path, session_factory=session_factory)

    assert result.sheet_kind is SheetKind.historical

    with session_factory() as s:
        all_cands = s.scalars(select(JobSupersessionCandidate)).all()
    assert all_cands == []


def test_historical_ingest_writes_no_candidates_when_live_jobs_exist(
    schd_workbook_factory, workbook_factory, session_factory
):
    """Historical ingest alongside live jobs: still no candidates opened."""
    # Phase 18b: pre-seed "9000A" so the live ingest processes inline.
    with session_factory() as s:
        s.add(Assembly(part_number="9000A"))
        s.commit()

    # Establish a live job first.
    live_wb = schd_workbook_factory(
        [{"data": {"JOB": "9000A\nNEW", "QTY": "1", "CUSTOMER": "LIVE-CO"}}]
    )
    ingest_workbook(live_wb, session_factory=session_factory)

    # Now ingest a historical workbook that references the same part.
    hist_wb = workbook_factory(
        [{"JOB": "9000A\nNEW", "QTY": "1", "CUSTOMER": "LIVE-CO",
          "SHIPPED": "04/01/2025"}]
    )
    result = ingest_workbook(hist_wb, session_factory=session_factory)

    assert result.sheet_kind is SheetKind.historical

    with session_factory() as s:
        all_cands = s.scalars(select(JobSupersessionCandidate)).all()
    assert all_cands == []
