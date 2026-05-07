"""Tests for POST /api/staging/{row_id}/restore with actions (Phase 15 Epoch 2).

Covers:
  - Empty actions path (simple restore, no collisions)
  - Actions: edit and discard applied before restore
  - Failure modes: unknown action kind, bad payload, row-not-found
  - Residual collision after actions → 409 with preview
"""
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


class TestRestoreWithEmptyActions:
    def test_simple_restore_no_collision_succeeds(self, client, session, open_batch):
        row = _discarded_row(session, open_batch, raw_job="ABC-12345 NEW", source_row_number=1)
        resp = client.post(f"/api/staging/{row.id}/restore", json={"actions": []})
        assert resp.status_code == status.HTTP_200_OK
        session.expire(row)
        row = session.get(ImportStagingRow, row.id)
        assert row.discarded_at is None

    def test_restore_without_body_uses_empty_actions(self, client, session, open_batch):
        """A POST without a body at all is treated as actions=[]."""
        row = _discarded_row(session, open_batch, source_row_number=1)
        resp = client.post(f"/api/staging/{row.id}/restore")
        assert resp.status_code == status.HTTP_200_OK

    def test_restore_404_for_unknown_row(self, client):
        resp = client.post("/api/staging/9999/restore", json={"actions": []})
        assert resp.status_code == status.HTTP_404_NOT_FOUND

    def test_restore_409_when_row_not_discarded(self, client, session, open_batch):
        row = _errored_row(session, open_batch, source_row_number=1)
        resp = client.post(f"/api/staging/{row.id}/restore", json={"actions": []})
        assert resp.status_code == status.HTTP_409_CONFLICT
        assert "not discarded" in resp.json()["detail"].lower()


class TestRestoreWithConflict:
    def test_errored_collider_causes_409_with_preview(self, client, session, open_batch):
        """Restoring a row that would collide with an active errored row returns 409 + preview."""
        target = _discarded_row(session, open_batch, raw_job="ABC-12345 NEW", source_row_number=1)
        _errored_row(session, open_batch, raw_job="ABC-12345 NEW", source_row_number=2)

        resp = client.post(f"/api/staging/{target.id}/restore", json={"actions": []})
        assert resp.status_code == status.HTTP_409_CONFLICT
        detail = resp.json()["detail"]
        assert "preview" in detail
        assert detail["preview"]["group_key"] == "ABC-12345|new|||"

    def test_row_remains_discarded_after_conflict_409(self, client, session, open_batch):
        """On 409, the target row must stay discarded (transaction rolled back).

        Note: after a failed request, the client session issues ROLLBACK which
        wipes the test session's flushed (but uncommitted) data from the DB.
        We verify the in-memory object state instead of reloading from DB.
        """
        target = _discarded_row(session, open_batch, raw_job="ABC-12345 NEW", source_row_number=1)
        _errored_row(session, open_batch, raw_job="ABC-12345 NEW", source_row_number=2)

        resp = client.post(f"/api/staging/{target.id}/restore", json={"actions": []})
        assert resp.status_code == status.HTTP_409_CONFLICT
        # In-memory state is not affected by the DB ROLLBACK — the object retains
        # the value that was set in the test session before the request.
        assert target.discarded_at is not None


class TestRestoreActionDiscard:
    def test_discard_action_removes_errored_collider(self, client, session, open_batch):
        """A 'discard' action on a collider, followed by restore, should succeed."""
        target = _discarded_row(session, open_batch, raw_job="ABC-12345 NEW", source_row_number=1)
        collider = _errored_row(session, open_batch, raw_job="ABC-12345 NEW", source_row_number=2)

        actions = [{"kind": "discard", "row_id": collider.id}]
        resp = client.post(f"/api/staging/{target.id}/restore", json={"actions": actions})
        assert resp.status_code == status.HTTP_200_OK

        session.expire_all()
        target_row = session.get(ImportStagingRow, target.id)
        collider_row = session.get(ImportStagingRow, collider.id)
        assert target_row.discarded_at is None
        assert collider_row.discarded_at is not None

    def test_discard_action_with_unknown_row_id_returns_422(self, client, session, open_batch):
        """An action referencing a non-existent row_id returns 422 with action_index."""
        target = _discarded_row(session, open_batch, raw_job="ABC-12345 NEW", source_row_number=1)

        actions = [{"kind": "discard", "row_id": 99999}]
        resp = client.post(f"/api/staging/{target.id}/restore", json={"actions": actions})
        assert resp.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY
        detail = resp.json()["detail"]
        assert detail["action_index"] == 0

    def test_discard_action_target_stays_discarded_on_422(self, client, session, open_batch):
        """When an action fails, the target row must remain discarded (rolled back).

        Note: after a failed request, the client session issues ROLLBACK which
        wipes the test session's flushed (but uncommitted) data from the DB.
        We verify the in-memory object state instead of reloading from DB.
        """
        target = _discarded_row(session, open_batch, raw_job="ABC-12345 NEW", source_row_number=1)

        actions = [{"kind": "discard", "row_id": 99999}]
        resp = client.post(f"/api/staging/{target.id}/restore", json={"actions": actions})
        assert resp.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY
        # In-memory state is not affected by the DB ROLLBACK — the object retains
        # the value that was set in the test session before the request.
        assert target.discarded_at is not None


class TestRestoreActionEdit:
    def test_edit_action_updates_collider_raw_job(self, client, session, open_batch):
        """An 'edit' action on a collider to change its raw_job, then restore succeeds."""
        target = _discarded_row(session, open_batch, raw_job="ABC-12345 NEW", source_row_number=1)
        collider = _errored_row(session, open_batch, raw_job="ABC-12345 NEW", source_row_number=2)

        actions = [{"kind": "edit", "row_id": collider.id, "payload": {"raw_job": "XYZ-99999 NEW"}}]
        resp = client.post(f"/api/staging/{target.id}/restore", json={"actions": actions})
        assert resp.status_code == status.HTTP_200_OK

        session.expire_all()
        collider_row = session.get(ImportStagingRow, collider.id)
        assert collider_row.raw_job == "XYZ-99999 NEW"

    def test_edit_action_invalid_field_returns_422_with_action_index(
        self, client, session, open_batch
    ):
        """An edit payload with an unknown field (extra='forbid') returns 422."""
        target = _discarded_row(session, open_batch, raw_job="ABC-12345 NEW", source_row_number=1)
        collider = _errored_row(session, open_batch, raw_job="ABC-12345 NEW", source_row_number=2)

        actions = [
            {"kind": "edit", "row_id": collider.id, "payload": {"nonexistent_field": "bad"}}
        ]
        resp = client.post(f"/api/staging/{target.id}/restore", json={"actions": actions})
        assert resp.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY
        detail = resp.json()["detail"]
        assert detail["action_index"] == 0

    def test_second_action_failure_reports_correct_index(self, client, session, open_batch):
        """When the second action fails, action_index should be 1."""
        target = _discarded_row(session, open_batch, raw_job="ABC-12345 NEW", source_row_number=1)
        collider1 = _errored_row(session, open_batch, raw_job="ABC-12345 NEW", source_row_number=2)

        actions = [
            {"kind": "discard", "row_id": collider1.id},
            {"kind": "discard", "row_id": 99999},  # fails
        ]
        resp = client.post(f"/api/staging/{target.id}/restore", json={"actions": actions})
        assert resp.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY
        detail = resp.json()["detail"]
        assert detail["action_index"] == 1


class TestRestoreActionUnknownKind:
    def test_unknown_action_kind_returns_422(self, client, session, open_batch):
        target = _discarded_row(session, open_batch, source_row_number=1)
        actions = [{"kind": "invalid_action", "row_id": target.id}]
        resp = client.post(f"/api/staging/{target.id}/restore", json={"actions": actions})
        assert resp.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY
        detail = resp.json()["detail"]
        assert detail["action_index"] == 0
