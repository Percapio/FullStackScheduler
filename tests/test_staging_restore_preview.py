"""Tests for GET /api/staging/{row_id}/restore-preview (Phase 15 Epoch 2)."""
from __future__ import annotations

from datetime import UTC, datetime

import pytest
from fastapi import status

from backend.app.models import ImportStagingRow, ImportStatus


def _errored_row(
    session,
    batch,
    *,
    source_row_number: int = 1,
    raw_job: str | None = "ABC-12345 NEW",
    **overrides,
) -> ImportStagingRow:
    row = ImportStagingRow(
        batch_id=batch.id,
        source_row_number=source_row_number,
        processing_status=ImportStatus.error,
        processing_error="test error",
        raw_job=raw_job,
        **overrides,
    )
    session.add(row)
    session.flush()
    return row


def _discarded_row(
    session,
    batch,
    *,
    source_row_number: int = 1,
    raw_job: str | None = "ABC-12345 NEW",
    **overrides,
) -> ImportStagingRow:
    row = _errored_row(
        session, batch,
        source_row_number=source_row_number,
        raw_job=raw_job,
        **overrides,
    )
    row.discarded_at = datetime(2026, 1, 1, tzinfo=UTC)
    session.flush()
    return row


class TestGetRestorePreviewErrors:
    def test_returns_404_for_missing_row(self, client):
        resp = client.get("/api/staging/9999/restore-preview")
        assert resp.status_code == status.HTTP_404_NOT_FOUND

    def test_returns_409_when_row_not_discarded(self, client, session, open_batch):
        row = _errored_row(session, open_batch, source_row_number=1)
        resp = client.get(f"/api/staging/{row.id}/restore-preview")
        assert resp.status_code == status.HTTP_409_CONFLICT
        assert "not discarded" in resp.json()["detail"].lower()


class TestGetRestorePreviewNoCollisions:
    def test_empty_preview_when_no_colliders(self, client, session, open_batch):
        """A discarded row with no colliders returns an empty preview."""
        row = _discarded_row(session, open_batch, source_row_number=1)
        resp = client.get(f"/api/staging/{row.id}/restore-preview")
        assert resp.status_code == status.HTTP_200_OK
        body = resp.json()
        assert body["colliding_staging_errored_rows"] == []
        assert body["colliding_staging_discarded_rows"] == []
        assert body["colliding_live_jobs"] == []

    def test_preview_group_key_matches_row(self, client, session, open_batch):
        row = _discarded_row(session, open_batch, raw_job="ABC-12345 NEW", source_row_number=1)
        resp = client.get(f"/api/staging/{row.id}/restore-preview")
        assert resp.status_code == status.HTTP_200_OK
        body = resp.json()
        assert body["group_key"] == "ABC-12345|new|||"

    def test_preview_incoming_is_staging_kind(self, client, session, open_batch):
        row = _discarded_row(session, open_batch, raw_job="ABC-12345 NEW", source_row_number=1)
        resp = client.get(f"/api/staging/{row.id}/restore-preview")
        assert resp.status_code == status.HTTP_200_OK
        body = resp.json()
        assert body["incoming"]["kind"] == "staging"
        assert body["incoming"]["staging"]["id"] == row.id

    def test_row_with_unparseable_job_returns_empty_group_key(self, client, session, open_batch):
        """A row whose raw_job cannot decompose gets empty group_key and no colliders."""
        row = _discarded_row(session, open_batch, raw_job="INVALID JOB STRING", source_row_number=1)
        resp = client.get(f"/api/staging/{row.id}/restore-preview")
        assert resp.status_code == status.HTTP_200_OK
        body = resp.json()
        assert body["group_key"] == ""
        assert body["colliding_staging_errored_rows"] == []


class TestGetRestorePreviewWithCollisions:
    def test_errored_collider_appears_in_errored_list(self, client, session, open_batch):
        """Another errored (non-discarded) row with same identity key is a class-(i) collision."""
        discarded = _discarded_row(session, open_batch, raw_job="ABC-12345 NEW", source_row_number=1)
        collider = _errored_row(session, open_batch, raw_job="ABC-12345 NEW", source_row_number=2)

        resp = client.get(f"/api/staging/{discarded.id}/restore-preview")
        assert resp.status_code == status.HTTP_200_OK
        body = resp.json()
        errored_ids = [r["id"] for r in body["colliding_staging_errored_rows"]]
        assert collider.id in errored_ids
        # Self must not appear in errored list
        assert discarded.id not in errored_ids

    def test_discarded_collider_appears_in_discarded_list(self, client, session, open_batch):
        """Another discarded row (not self) with the same key is a class-(ii) collision."""
        target = _discarded_row(session, open_batch, raw_job="ABC-12345 NEW", source_row_number=1)
        other = _discarded_row(session, open_batch, raw_job="ABC-12345 NEW", source_row_number=2)

        resp = client.get(f"/api/staging/{target.id}/restore-preview")
        assert resp.status_code == status.HTTP_200_OK
        body = resp.json()
        discarded_ids = [r["id"] for r in body["colliding_staging_discarded_rows"]]
        assert other.id in discarded_ids
        # Self must not appear as its own discarded collider
        assert target.id not in discarded_ids

    def test_row_not_in_its_own_errored_or_discarded_lists(self, client, session, open_batch):
        """The target row must never appear in any collision list."""
        row = _discarded_row(session, open_batch, raw_job="ABC-12345 NEW", source_row_number=1)
        resp = client.get(f"/api/staging/{row.id}/restore-preview")
        body = resp.json()
        all_ids = (
            [r["id"] for r in body["colliding_staging_errored_rows"]]
            + [r["id"] for r in body["colliding_staging_discarded_rows"]]
        )
        assert row.id not in all_ids

    def test_different_identity_key_is_not_a_collision(self, client, session, open_batch):
        """A row with a different identity key must not appear in any collision list."""
        discarded = _discarded_row(session, open_batch, raw_job="ABC-12345 NEW", source_row_number=1)
        _errored_row(session, open_batch, raw_job="XYZ-99999 NEW", source_row_number=2)

        resp = client.get(f"/api/staging/{discarded.id}/restore-preview")
        assert resp.status_code == status.HTTP_200_OK
        body = resp.json()
        assert body["colliding_staging_errored_rows"] == []

    def test_multiple_errored_colliders_all_appear(self, client, session, open_batch):
        """Multiple errored rows sharing the same identity key are all reported."""
        discarded = _discarded_row(session, open_batch, raw_job="ABC-12345 NEW", source_row_number=1)
        c1 = _errored_row(session, open_batch, raw_job="ABC-12345 NEW", source_row_number=2)
        c2 = _errored_row(session, open_batch, raw_job="ABC-12345 NEW", source_row_number=3)

        resp = client.get(f"/api/staging/{discarded.id}/restore-preview")
        assert resp.status_code == status.HTTP_200_OK
        body = resp.json()
        errored_ids = {r["id"] for r in body["colliding_staging_errored_rows"]}
        assert {c1.id, c2.id} == errored_ids

    def test_discarded_row_not_in_errored_list(self, client, session, open_batch):
        """A discarded row with the same key must not appear in the errored list."""
        target = _discarded_row(session, open_batch, raw_job="ABC-12345 NEW", source_row_number=1)
        other_discarded = _discarded_row(
            session, open_batch, raw_job="ABC-12345 NEW", source_row_number=2,
        )

        resp = client.get(f"/api/staging/{target.id}/restore-preview")
        assert resp.status_code == status.HTTP_200_OK
        body = resp.json()
        errored_ids = [r["id"] for r in body["colliding_staging_errored_rows"]]
        assert other_discarded.id not in errored_ids
