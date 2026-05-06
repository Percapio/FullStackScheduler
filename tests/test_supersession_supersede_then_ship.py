"""Cross-epoch test: supersede then ship (audit #2).

Sequence:
  1. Ingest a live workbook with bare 5000Z NEW.
  2. Ingest a live workbook with 5000Z-1par NEW + 5000Z-2par NEW.
     One pending candidate appears for the bare row.
  3. Operator approves the candidate. Bare-5000Z Job is superseded.
  4. Ingest a SHIPPED (AA) workbook containing 5000Z NEW with a shipped date.
  5. Assert: a *new* active Job exists for identity 5000Z NEW with shipped_at set.
  6. Assert: the superseded predecessor is unchanged (still has superseded_at).
"""
from __future__ import annotations

from datetime import date

from sqlalchemy import select

from backend.app.ingest import ingest_workbook
from backend.app.models import Assembly, Job, JobSupersessionCandidate


def test_supersede_then_ship_creates_new_active_job(
    schd_workbook_factory,
    workbook_factory,
    session_factory,
    client,
):
    # Step 1: live v1 — bare 5000Z NEW.
    wb1 = schd_workbook_factory(
        [{"data": {"JOB": "5000Z\nNEW", "QTY": "1", "CUSTOMER": "TESTCO"}}],
        filename="live_v1.xlsx",
    )
    r1 = ingest_workbook(wb1, session_factory=session_factory)
    assert r1.candidates_opened == 0

    # Step 2: live v2 — split into -1par + -2par.
    wb2 = schd_workbook_factory(
        [
            {"data": {"JOB": "5000Z-1par\nNEW", "QTY": "1", "CUSTOMER": "TESTCO"}},
            {"data": {"JOB": "5000Z-2par\nNEW", "QTY": "1", "CUSTOMER": "TESTCO"}},
        ],
        filename="live_v2.xlsx",
    )
    r2 = ingest_workbook(wb2, session_factory=session_factory)
    assert r2.candidates_opened == 1

    # Locate the pending candidate for the bare 5000Z job.
    with session_factory() as s:
        asm = s.scalars(
            select(Assembly).where(Assembly.part_number == "5000Z")
        ).one()
        bare_job = s.scalars(
            select(Job)
            .where(Job.assembly_id == asm.id)
            .where(Job.split_suffix.is_(None))
            .where(Job.superseded_at.is_(None))
        ).one()
        bare_job_id = bare_job.id

        cand = s.scalars(
            select(JobSupersessionCandidate)
            .where(JobSupersessionCandidate.job_id == bare_job_id)
            .where(JobSupersessionCandidate.resolved_at.is_(None))
        ).one()
        cand_id = cand.id

    # Step 3: operator approves via API.
    resp = client.post(f"/api/staging/supersession-candidates/{cand_id}/approve")
    assert resp.status_code == 200
    assert resp.json()["resolution"] == "approve"

    # Confirm bare job is now superseded.
    with session_factory() as s:
        superseded_job = s.get(Job, bare_job_id)
        assert superseded_job.superseded_at is not None

    # Step 4: SHIPPED (AA) workbook for the same identity.
    hist_wb = workbook_factory(
        [{"JOB": "5000Z\nNEW", "QTY": "1", "CUSTOMER": "TESTCO",
          "SHIPPED": "04/15/2026"}]
    )
    r3 = ingest_workbook(hist_wb, session_factory=session_factory)
    assert r3.rows_inserted == 1  # new Job created (slot freed by supersession)

    # Step 5: new active Job for the same identity with shipped_at set.
    with session_factory() as s:
        new_job = s.scalars(
            select(Job)
            .where(Job.assembly_id == asm.id)
            .where(Job.split_suffix.is_(None))
            .where(Job.superseded_at.is_(None))
        ).one()
        assert new_job.id != bare_job_id
        assert new_job.shipped_at == date(2026, 4, 15)

    # Step 6: superseded predecessor is unchanged.
    with session_factory() as s:
        predecessor = s.get(Job, bare_job_id)
        assert predecessor.superseded_at is not None
        assert predecessor.shipped_at is None  # was never shipped directly
