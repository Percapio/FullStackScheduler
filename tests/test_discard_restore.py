"""Contract tests for the discard / restore endpoints (Phase 2)."""
from __future__ import annotations

from datetime import UTC, datetime

import pytest
from fastapi import status

from backend.app.models import ImportStagingRow, ImportStatus


def _errored_row(session, batch, **overrides) -> ImportStagingRow:
    defaults = dict(
        batch_id=batch.id,
        source_row_number=1,
        processing_status=ImportStatus.error,
        processing_error="test error",
    )
    defaults.update(overrides)
    row = ImportStagingRow(**defaults)
    session.add(row)
    session.flush()
    return row


class TestDiscardEndpoint:
    def test_discard_endpoint_soft_deletes(self, client, session, open_batch):
        row = _errored_row(session, open_batch)

        resp = client.delete(f"/api/staging/{row.id}")
        assert resp.status_code == status.HTTP_204_NO_CONTENT

        session.expire_all()
        row = session.get(ImportStagingRow, row.id)
        assert row.discarded_at is not None

        errored = client.get("/api/staging/errored").json()
        assert not any(r["id"] == row.id for r in errored)

    def test_discard_idempotent_on_already_discarded(self, client, session, open_batch):
        row = _errored_row(session, open_batch)
        first = datetime(2026, 1, 1, tzinfo=UTC)
        row.discarded_at = first
        session.commit()  # commit so the client session rollback won't wipe it

        resp = client.delete(f"/api/staging/{row.id}")
        assert resp.status_code == status.HTTP_204_NO_CONTENT

        # Reload after the API call to confirm the original timestamp was preserved.
        # SQLite strips tzinfo on roundtrip, so compare naive values.
        session.expire(row)
        reloaded = session.get(ImportStagingRow, row.id)
        stored = reloaded.discarded_at
        stored_naive = stored.replace(tzinfo=None) if stored.tzinfo else stored
        assert stored_naive == first.replace(tzinfo=None)

    def test_discard_refuses_resolved_row(self, client, session, open_batch, seeded_processed_row):
        resp = client.delete(f"/api/staging/{seeded_processed_row.id}")
        assert resp.status_code == status.HTTP_409_CONFLICT
        assert "resolved" in resp.json()["detail"].lower()

        # discarded_at was never written — check in-memory state directly
        # (avoid expire_all which would trigger a reload after client's rollback).
        assert seeded_processed_row.discarded_at is None

    def test_discard_refuses_pending_row(self, client, session, open_batch):
        row = _errored_row(
            session, open_batch,
            processing_status=ImportStatus.pending,
            processing_error=None,
        )

        resp = client.delete(f"/api/staging/{row.id}")
        assert resp.status_code == status.HTTP_409_CONFLICT
        assert "only errored" in resp.json()["detail"].lower()

        # discarded_at was never written — check in-memory state directly.
        assert row.discarded_at is None

    def test_discard_refuses_imported_row(self, client, session, open_batch):
        # Synthetic: an imported-status row without a resolved_job_id.
        row = _errored_row(
            session, open_batch,
            processing_status=ImportStatus.processed,
            processing_error=None,
        )
        # Manually clear resolved_job_id so only the status guard fires.
        row.resolved_job_id = None
        session.flush()

        resp = client.delete(f"/api/staging/{row.id}")
        assert resp.status_code == status.HTTP_409_CONFLICT
        assert "only errored" in resp.json()["detail"].lower()

        # discarded_at was never written — check in-memory state directly.
        assert row.discarded_at is None

    def test_discard_404_on_missing(self, client):
        resp = client.delete("/api/staging/99999")
        assert resp.status_code == status.HTTP_404_NOT_FOUND

    def test_get_row_returns_discarded_row(self, client, session, open_batch):
        """GET /{row_id} is unfiltered — single-row reads return discarded rows unchanged."""
        row = _errored_row(session, open_batch)
        client.delete(f"/api/staging/{row.id}")

        resp = client.get(f"/api/staging/{row.id}")
        assert resp.status_code == status.HTTP_200_OK
        assert resp.json()["discarded_at"] is not None

    def test_discard_persists_across_session(self, client, session, open_batch):
        row = _errored_row(session, open_batch)

        resp = client.delete(f"/api/staging/{row.id}")
        assert resp.status_code == status.HTTP_204_NO_CONTENT

        # The service committed; reload to confirm the state is durable.
        session.expire(row)
        reloaded = session.get(ImportStagingRow, row.id)
        assert reloaded.discarded_at is not None

        errored = client.get("/api/staging/errored").json()
        assert not any(r["id"] == row.id for r in errored)

    def test_correct_endpoint_refuses_discarded_row(self, client, session, open_batch):
        row = _errored_row(
            session, open_batch,
            raw_job="bad-cell",
            processing_error="Invalid JOB cell: 'bad-cell'",
        )
        client.delete(f"/api/staging/{row.id}")

        resp = client.post(
            f"/api/staging/{row.id}/correct",
            json={"raw_job": "137845\nNEW", "raw_qty": "1", "raw_customer": "ACME"},
        )
        assert resp.status_code == status.HTTP_409_CONFLICT
        assert "discarded" in resp.json()["detail"].lower()

        session.expire_all()
        row = session.get(ImportStagingRow, row.id)
        assert row.raw_job == "bad-cell"


class TestRestoreEndpoint:
    def test_restore_clears_discarded_at(self, client, session, open_batch):
        row = _errored_row(session, open_batch)
        client.delete(f"/api/staging/{row.id}")

        resp = client.post(f"/api/staging/{row.id}/restore")
        assert resp.status_code == status.HTTP_200_OK
        assert resp.json()["discarded_at"] is None

        errored = client.get("/api/staging/errored").json()
        assert any(r["id"] == row.id for r in errored)

    def test_restore_404_on_missing(self, client):
        resp = client.post("/api/staging/99999/restore")
        assert resp.status_code == status.HTTP_404_NOT_FOUND

    def test_restore_409_if_not_discarded(self, client, session, open_batch):
        row = _errored_row(session, open_batch)

        resp = client.post(f"/api/staging/{row.id}/restore")
        assert resp.status_code == status.HTTP_409_CONFLICT

    def test_discard_then_restore_round_trip(self, client, session, open_batch):
        """Errored row → discard → restore → correct succeeds end-to-end."""
        row = _errored_row(
            session, open_batch,
            raw_job="not-a-job-cell",
            processing_error="Invalid JOB cell: 'not-a-job-cell'",
        )
        assert client.delete(f"/api/staging/{row.id}").status_code == 204
        assert client.post(f"/api/staging/{row.id}/restore").status_code == 200

        resp = client.post(
            f"/api/staging/{row.id}/correct",
            json={"raw_job": "137845\nNEW", "raw_qty": "1", "raw_customer": "ACME"},
        )
        assert resp.status_code == status.HTTP_200_OK
        assert resp.json()["assembly"]["part_number"] == "137845"
