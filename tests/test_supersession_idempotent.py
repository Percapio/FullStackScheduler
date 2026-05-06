"""Detection idempotency — re-running against the same batch is a no-op.

When a pending candidate exists for a job, re-ingesting the same batch
(via --force) does not mint a duplicate.
"""
from __future__ import annotations

from sqlalchemy import select

from backend.app.ingest import ingest_workbook
from backend.app.models import JobSupersessionCandidate


def test_rerunning_detection_is_noop_when_candidate_is_still_pending(
    schd_workbook_factory, session_factory
):
    # Establish a split: bare job becomes orphaned.
    wb1 = schd_workbook_factory(
        [{"data": {"JOB": "140000\nNEW", "QTY": "1", "CUSTOMER": "ACME"}}],
        filename="idem_v1.xlsx",
    )
    ingest_workbook(wb1, session_factory=session_factory)

    wb2 = schd_workbook_factory(
        [
            {"data": {"JOB": "140000-1par\nNEW", "QTY": "1", "CUSTOMER": "ACME"}},
            {"data": {"JOB": "140000-2par\nNEW", "QTY": "1", "CUSTOMER": "ACME"}},
        ],
        filename="idem_v2.xlsx",
    )
    r2 = ingest_workbook(wb2, session_factory=session_factory)
    assert r2.candidates_opened == 1

    # Re-ingest v2 with --force. The pending candidate is still open, so
    # detection must skip minting a new one.
    r2b = ingest_workbook(wb2, force=True, session_factory=session_factory)
    assert r2b.candidates_opened == 0
    assert r2b.candidates_auto_returned == 0

    # Exactly one candidate exists, not two.
    with session_factory() as s:
        all_cands = s.scalars(select(JobSupersessionCandidate)).all()
    assert len(all_cands) == 1
    assert all_cands[0].resolved_at is None
