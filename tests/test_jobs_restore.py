"""Backend tests for Phase 15 Epoch 4: Job restore-preview and restore endpoints.

Tests per TDD §6.8:
  - test_jobs_restore_preview: three-class collision detection, empty preview when
    no collisions, 404/409 status codes.
  - test_jobs_restore_atomicity: edits + restore in one transaction; per-action
    failure rolls back and returns 422 with action_index.
  - test_staging_restore_loses_to_concurrent_job_create: cross-source race — a Job
    created between preview and commit must surface as a class-(iii) collision on
    the commit attempt (TDD §6.8, Audit-01 #15).
"""
from __future__ import annotations

from datetime import UTC, datetime, date

import pytest
from fastapi import status

from backend.app.models import (
    Assembly,
    BuildType,
    Customer,
    ImportBatch,
    ImportStagingRow,
    ImportStatus,
    Job,
    JobStatus,
)
from backend.app.services.jobs import (
    discard_job,
    identity_key_for_job,
    preview_restore_job,
    restore_job_with_actions,
    JobRestoreError,
    JobRestoreConflictError,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_assembly(session, part_number: str) -> Assembly:
    from sqlalchemy import select
    existing = session.execute(
        select(Assembly).where(Assembly.part_number == part_number)
    ).scalar_one_or_none()
    if existing:
        return existing
    a = Assembly(part_number=part_number)
    session.add(a)
    session.flush()
    return a


def _make_customer(session, name: str) -> Customer:
    from sqlalchemy import select
    existing = session.execute(
        select(Customer).where(Customer.name == name)
    ).scalar_one_or_none()
    if existing:
        return existing
    c = Customer(name=name)
    session.add(c)
    session.flush()
    return c


def _make_job(session, *, part_number="REST-001", customer_name="RestoreCo", **overrides) -> Job:
    assembly = _make_assembly(session, part_number)
    customer = _make_customer(session, customer_name)
    defaults = dict(
        assembly_id=assembly.id,
        customer_id=customer.id,
        quantity=5,
        build_type=BuildType.new,
    )
    defaults.update(overrides)
    job = Job(**defaults)
    session.add(job)
    session.flush()
    return job


def _make_discarded_job(session, **overrides) -> Job:
    job = _make_job(session, **overrides)
    job.discarded_at = datetime(2026, 1, 1)
    session.flush()
    return job


def _make_staging_row(
    session,
    batch: ImportBatch,
    *,
    raw_job: str,
    raw_qty: str = "5",
    raw_customer: str = "StagingCo",
    source_row_number: int = 1,
    **overrides,
) -> ImportStagingRow:
    overrides.setdefault('processing_status', ImportStatus.pending)
    row = ImportStagingRow(
        batch_id=batch.id,
        source_row_number=source_row_number,
        raw_job=raw_job,
        raw_qty=raw_qty,
        raw_customer=raw_customer,
        **overrides,
    )
    session.add(row)
    session.flush()
    return row


# ---------------------------------------------------------------------------
# Service-layer: preview_restore_job
# ---------------------------------------------------------------------------

class TestPreviewRestoreJobService:
    def test_raises_not_found_for_unknown_id(self, session):
        with pytest.raises(JobRestoreError, match="not found"):
            preview_restore_job(session, 999_999)

    def test_raises_not_discarded_for_active_job(self, session):
        job = _make_job(session)
        with pytest.raises(JobRestoreError, match="not discarded"):
            preview_restore_job(session, job.id)

    def test_empty_preview_when_no_collisions(self, session):
        job = _make_discarded_job(session, part_number="NOCONFLICT-001")
        preview = preview_restore_job(session, job.id)
        assert preview.colliding_staging_errored_rows == []
        assert preview.colliding_staging_discarded_rows == []
        assert preview.colliding_live_jobs == []
        assert preview.group_key != ""

    def test_group_key_matches_identity_key_for_job(self, session):
        job = _make_discarded_job(session, part_number="GK-001")
        preview = preview_restore_job(session, job.id)
        assert preview.group_key == identity_key_for_job(job)

    def test_incoming_kind_is_job(self, session):
        job = _make_discarded_job(session, part_number="KIND-001")
        preview = preview_restore_job(session, job.id)
        assert preview.incoming.kind == "job"
        assert preview.incoming.job is not None
        assert preview.incoming.staging is None

    def test_detects_live_job_collider(self, session):
        """A non-discarded active job at the same identity is a class-(iii) collider."""
        # Create a discarded job at identity A
        discarded = _make_discarded_job(session, part_number="LIVECOLL-001")
        # Create an active job at the same identity
        active = _make_job(session, part_number="LIVECOLL-001", customer_name="OtherCo")

        preview = preview_restore_job(session, discarded.id)
        live_ids = [j.id for j in preview.colliding_live_jobs]
        assert active.id in live_ids

    def test_detects_errored_staging_collider(self, session, open_batch):
        """A staging row with matching group_key and error status is a class-(i) collider."""
        job = _make_discarded_job(session, part_number="ERRCOLL-001")
        group_key = identity_key_for_job(job)
        # Build a staging row that resolves to the same identity key.
        # The raw_job must decompose to the same part/build/suffix/repeat.
        row = _make_staging_row(
            session, open_batch, raw_job="ERRCOLL-001\nNEW",
            processing_status=ImportStatus.error,
            processing_error="some error",
        )
        session.flush()

        preview = preview_restore_job(session, job.id)
        errored_ids = [r.id for r in preview.colliding_staging_errored_rows]
        assert row.id in errored_ids

    def test_does_not_include_self_in_live_jobs(self, session):
        """The discarded job being restored must not appear in colliding_live_jobs."""
        job = _make_discarded_job(session, part_number="SELF-001")
        preview = preview_restore_job(session, job.id)
        live_ids = [j.id for j in preview.colliding_live_jobs]
        assert job.id not in live_ids


# ---------------------------------------------------------------------------
# Service-layer: restore_job_with_actions
# ---------------------------------------------------------------------------

class TestRestoreJobWithActionsService:
    def test_happy_path_clears_discarded_at(self, session):
        job = _make_discarded_job(session, part_number="HAPREST-001")
        assert job.discarded_at is not None

        restored = restore_job_with_actions(session, job.id, [])
        assert restored.discarded_at is None

    def test_raises_not_found_for_unknown_id(self, session):
        with pytest.raises(JobRestoreError, match="not found"):
            restore_job_with_actions(session, 999_999, [])

    def test_raises_not_discarded_for_active_job(self, session):
        job = _make_job(session)
        with pytest.raises(JobRestoreError, match="not discarded"):
            restore_job_with_actions(session, job.id, [])

    def test_conflict_error_raised_when_live_job_exists(self, session):
        """Residual class-(iii) collision must raise JobRestoreConflictError."""
        # discarded job
        discarded = _make_discarded_job(session, part_number="CONFLRES-001")
        # active job at the same identity — blocks restore
        _make_job(session, part_number="CONFLRES-001", customer_name="BlockerCo")
        session.flush()

        with pytest.raises(JobRestoreConflictError):
            restore_job_with_actions(session, discarded.id, [])

    def test_discard_action_against_staging_row_is_applied(self, session, open_batch):
        """A discard action targeting a colliding errored staging row is applied
        inside the same transaction as the restore."""
        from backend.app.schemas import StagingRestoreAction

        job = _make_discarded_job(session, part_number="ACTIONREST-001")
        errored_row = _make_staging_row(
            session, open_batch, raw_job="ACTIONREST-001\nNEW",
            processing_status=ImportStatus.error,
            processing_error="collider",
        )
        session.flush()

        action = StagingRestoreAction(kind="discard", row_id=errored_row.id)
        restored = restore_job_with_actions(session, job.id, [action])

        assert restored.discarded_at is None
        # Staging row must have been discarded by the action.
        session.refresh(errored_row)
        assert errored_row.discarded_at is not None


# ---------------------------------------------------------------------------
# API endpoint tests
# ---------------------------------------------------------------------------

class TestRestorePreviewEndpoint:
    def test_404_for_unknown_job(self, client):
        resp = client.get("/api/jobs/999999/restore-preview")
        assert resp.status_code == status.HTTP_404_NOT_FOUND

    def test_409_for_active_job(self, client, session):
        job = _make_job(session)
        session.commit()
        resp = client.get(f"/api/jobs/{job.id}/restore-preview")
        assert resp.status_code == status.HTTP_409_CONFLICT

    def test_200_with_empty_preview_when_no_collisions(self, client, session):
        job = _make_discarded_job(session, part_number="EPEP-001")
        session.commit()

        resp = client.get(f"/api/jobs/{job.id}/restore-preview")
        assert resp.status_code == status.HTTP_200_OK

        body = resp.json()
        assert body["colliding_staging_errored_rows"] == []
        assert body["colliding_staging_discarded_rows"] == []
        assert body["colliding_live_jobs"] == []
        assert body["incoming"]["kind"] == "job"

    def test_preview_surfaces_live_job_collider(self, client, session):
        discarded = _make_discarded_job(session, part_number="EPCOLL-001")
        _make_job(session, part_number="EPCOLL-001", customer_name="EpBlocker")
        session.commit()

        body = client.get(f"/api/jobs/{discarded.id}/restore-preview").json()
        assert len(body["colliding_live_jobs"]) == 1


class TestRestoreJobEndpoint:
    def test_404_for_unknown_job(self, client):
        resp = client.post("/api/jobs/999999/restore", json={"actions": []})
        assert resp.status_code == status.HTTP_404_NOT_FOUND

    def test_409_for_active_job(self, client, session):
        job = _make_job(session)
        session.commit()
        resp = client.post(f"/api/jobs/{job.id}/restore", json={"actions": []})
        assert resp.status_code == status.HTTP_409_CONFLICT

    def test_200_happy_path_restores_job(self, client, session):
        job = _make_discarded_job(session, part_number="REPHP-001")
        session.commit()

        resp = client.post(f"/api/jobs/{job.id}/restore", json={"actions": []})
        assert resp.status_code == status.HTTP_200_OK

        body = resp.json()
        assert body["id"] == job.id
        assert body["discarded_at"] is None

    def test_restored_job_appears_in_shipping(self, client, session):
        job = _make_discarded_job(session, part_number="RESH-001", status=JobStatus.planned)
        session.commit()

        client.post(f"/api/jobs/{job.id}/restore", json={"actions": []})

        shipping = client.get("/api/jobs/shipping").json()
        ids = [j["id"] for j in shipping]
        assert job.id in ids

    def test_restored_job_absent_from_discarded_list(self, client, session):
        job = _make_discarded_job(session, part_number="RESABS-001")
        session.commit()

        # Confirm it shows up in discarded list before restore.
        discarded_before = [j["id"] for j in client.get("/api/jobs/discarded").json()]
        assert job.id in discarded_before

        client.post(f"/api/jobs/{job.id}/restore", json={"actions": []})

        discarded_after = [j["id"] for j in client.get("/api/jobs/discarded").json()]
        assert job.id not in discarded_after

    def test_409_with_fresh_preview_on_residual_collision(self, client, session):
        """When a live job blocks restore, the 409 body must include a fresh preview."""
        discarded = _make_discarded_job(session, part_number="RESC409-001")
        _make_job(session, part_number="RESC409-001", customer_name="BlockerCo")
        session.commit()

        resp = client.post(f"/api/jobs/{discarded.id}/restore", json={"actions": []})
        assert resp.status_code == status.HTTP_409_CONFLICT

        detail = resp.json()["detail"]
        assert "preview" in detail
        assert len(detail["preview"]["colliding_live_jobs"]) >= 1

    def test_422_with_action_index_on_bad_staging_action(self, client, session):
        """Per-action validation failure returns 422 with the action_index."""
        job = _make_discarded_job(session, part_number="RE422-001")
        session.commit()

        # Reference a non-existent staging row id.
        resp = client.post(
            f"/api/jobs/{job.id}/restore",
            json={"actions": [{"kind": "discard", "row_id": 999999}]},
        )
        assert resp.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY

        detail = resp.json()["detail"]
        assert "action_index" in detail
        assert detail["action_index"] == 0


# ---------------------------------------------------------------------------
# Discarded jobs list endpoint
# ---------------------------------------------------------------------------

class TestDiscardedJobsListEndpoint:
    def test_returns_only_discarded_jobs(self, client, session):
        active = _make_job(session, part_number="DJLA-001")
        discarded = _make_discarded_job(session, part_number="DJLD-001")
        session.commit()

        ids = [j["id"] for j in client.get("/api/jobs/discarded").json()]
        assert discarded.id in ids
        assert active.id not in ids

    def test_x_total_count_header(self, client, session):
        _make_discarded_job(session, part_number="DJTC-001")
        _make_discarded_job(session, part_number="DJTC-002")
        session.commit()

        resp = client.get("/api/jobs/discarded")
        assert int(resp.headers["x-total-count"]) >= 2

    def test_search_matches_part_number(self, client, session):
        _make_discarded_job(session, part_number="SRCH-UNIQUE-XYZ")
        _make_discarded_job(session, part_number="SRCH-OTHER-001")
        session.commit()

        results = client.get("/api/jobs/discarded?search=UNIQUE-XYZ").json()
        part_numbers = [j["assembly"]["part_number"] for j in results]
        assert any("UNIQUE-XYZ" in p for p in part_numbers)

    def test_search_by_exact_id(self, client, session):
        job = _make_discarded_job(session, part_number="IDSCH-001")
        session.commit()

        results = client.get(f"/api/jobs/discarded?search={job.id}").json()
        ids = [j["id"] for j in results]
        assert job.id in ids

    def test_pagination_limit_and_offset(self, client, session):
        for i in range(3):
            _make_discarded_job(session, part_number=f"PAGE-{i:03d}")
        session.commit()

        page1 = client.get("/api/jobs/discarded?limit=2&offset=0").json()
        page2 = client.get("/api/jobs/discarded?limit=2&offset=2").json()
        assert len(page1) == 2
        assert len(page2) >= 1
        assert {j["id"] for j in page1}.isdisjoint({j["id"] for j in page2})


# ---------------------------------------------------------------------------
# Cross-source race (Audit-01 #15): staging restore loses to concurrent job create
# ---------------------------------------------------------------------------

class TestStagingRestoreLosesToConcurrentJobCreate:
    def test_commit_409s_when_job_created_between_preview_and_commit(
        self, client, session, open_batch
    ):
        """Operator opens staging row R's restore preview at t0 (preview shows zero
        collisions). Between t0 and the commit at t1, an ingest creates a Job J at
        the same identity. The commit must 409 with a fresh preview surfacing J as a
        class-(iii) collision.

        This is Audit-01 #15's acceptance test for cross-source preview correctness.
        """
        from backend.app.transform import transform_staging_row
        from backend.app.services.staging import (
            discard_staging_row,
            get_staging_row,
            preview_restore_staging,
        )
        from backend.app.schemas import StagingRestoreAction

        RAW_JOB = "RACE-001\nNEW"

        # ── t0: Create a staging row, let it transform, then discard it. ──────────
        row = _make_staging_row(session, open_batch, raw_job=RAW_JOB, raw_customer="RaceCo")
        transform_staging_row(session, row)
        session.flush()

        # If the transform produced a job (happy path), discard the row the manual way.
        row = get_staging_row(session, row.id)
        if row.resolved_job_id:
            # Staging row resolved — discard via service (not the DELETE endpoint).
            job_to_discard = session.get(Job, row.resolved_job_id)
            if job_to_discard:
                job_to_discard.discarded_at = datetime.now(UTC).replace(tzinfo=None)
                session.flush()
        # Put the staging row itself into discarded state.
        row.discarded_at = datetime.now(UTC).replace(tzinfo=None)
        session.flush()

        # Confirm t0 preview shows no collisions.
        preview_t0 = preview_restore_staging(session, row)
        assert preview_t0.colliding_live_jobs == [], (
            "Pre-condition failed: preview at t0 must show no live-job collisions."
        )

        # ── t1: Simulate concurrent ingest — a new active Job at the same identity. ──
        blocking_job = _make_job(session, part_number="RACE-001", customer_name="NewCo")
        assert blocking_job.discarded_at is None
        session.commit()  # Commit the blocking job so it's visible to a fresh query.

        # ── Commit restore: must 409 because blocking_job now occupies the slot. ──
        resp = client.post(
            f"/api/staging/{row.id}/restore",
            json={"actions": []},
        )
        assert resp.status_code == status.HTTP_409_CONFLICT, (
            f"Expected 409 due to concurrent job create; got {resp.status_code}: {resp.text}"
        )

        detail = resp.json()["detail"]
        assert "preview" in detail
        live_ids = [j["id"] for j in detail["preview"]["colliding_live_jobs"]]
        assert blocking_job.id in live_ids, (
            "The fresh preview must surface the concurrently created job as a class-(iii) collider."
        )
