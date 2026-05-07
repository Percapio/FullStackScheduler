"""Contract tests for GET /api/staging/discarded (Phase 2)."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from fastapi import status

from backend.app.models import ImportStagingRow, ImportStatus


def _errored_row(session, batch, source_row_number=1, **overrides) -> ImportStagingRow:
    defaults = dict(
        batch_id=batch.id,
        source_row_number=source_row_number,
        processing_status=ImportStatus.error,
        processing_error="test error",
    )
    defaults.update(overrides)
    row = ImportStagingRow(**defaults)
    session.add(row)
    session.flush()
    return row


def _discard(row: ImportStagingRow, session, ts: datetime | None = None) -> None:
    row.discarded_at = ts or datetime.now(UTC)
    session.flush()


class TestListDiscarded:
    def test_list_discarded_returns_only_discarded(self, client, session, open_batch):
        r1 = _errored_row(session, open_batch, source_row_number=1)
        r2 = _errored_row(session, open_batch, source_row_number=2)
        r3 = _errored_row(session, open_batch, source_row_number=3)
        _discard(r1, session)
        _discard(r2, session)

        resp = client.get("/api/staging/discarded")
        assert resp.status_code == status.HTTP_200_OK
        body = resp.json()
        ids = {r["id"] for r in body}
        assert ids == {r1.id, r2.id}
        assert r3.id not in ids
        assert int(resp.headers["x-total-count"]) == 2

    def test_list_discarded_newest_first(self, client, session, open_batch):
        base = datetime(2026, 5, 1, 12, 0, 0, tzinfo=UTC)
        r_a = _errored_row(session, open_batch, source_row_number=1)
        r_b = _errored_row(session, open_batch, source_row_number=2)
        r_c = _errored_row(session, open_batch, source_row_number=3)
        _discard(r_a, session, base)
        _discard(r_b, session, base + timedelta(seconds=1))
        _discard(r_c, session, base + timedelta(seconds=2))

        resp = client.get("/api/staging/discarded")
        ids = [r["id"] for r in resp.json()]
        assert ids == [r_c.id, r_b.id, r_a.id]

    def test_list_discarded_pagination(self, client, session, open_batch):
        base = datetime(2026, 5, 1, 12, 0, 0, tzinfo=UTC)
        rows = []
        for i in range(5):
            r = _errored_row(session, open_batch, source_row_number=i + 1)
            _discard(r, session, base + timedelta(seconds=i))
            rows.append(r)
        # Commit so each API call's session rollback doesn't wipe setup data.
        session.commit()

        page0 = client.get("/api/staging/discarded?limit=2&offset=0")
        assert page0.status_code == status.HTTP_200_OK
        assert len(page0.json()) == 2
        assert int(page0.headers["x-total-count"]) == 5

        page1 = client.get("/api/staging/discarded?limit=2&offset=2")
        assert len(page1.json()) == 2
        assert int(page1.headers["x-total-count"]) == 5

        page2 = client.get("/api/staging/discarded?limit=2&offset=4")
        assert len(page2.json()) == 1
        assert int(page2.headers["x-total-count"]) == 5

    def test_list_discarded_excludes_active(self, client, session, open_batch):
        _errored_row(session, open_batch, source_row_number=1)
        _errored_row(session, open_batch, source_row_number=2)
        _errored_row(session, open_batch, source_row_number=3)

        resp = client.get("/api/staging/discarded")
        assert resp.status_code == status.HTTP_200_OK
        assert resp.json() == []
        assert int(resp.headers["x-total-count"]) == 0

    def test_get_discarded_route_not_captured_by_path_param(self, client):
        """Regression: GET /discarded must not be matched as GET /{row_id}=discarded (422)."""
        resp = client.get("/api/staging/discarded")
        assert resp.status_code == status.HTTP_200_OK
        assert isinstance(resp.json(), list)

    def test_search_filters_by_processing_error(self, client, session, open_batch):
        r1 = _errored_row(session, open_batch, source_row_number=1,
                          processing_error="unique_needle error")
        r2 = _errored_row(session, open_batch, source_row_number=2,
                          processing_error="other error")
        _discard(r1, session)
        _discard(r2, session)
        session.commit()

        resp = client.get("/api/staging/discarded?search=unique_needle")
        assert resp.status_code == 200
        ids = {r["id"] for r in resp.json()}
        assert ids == {r1.id}
        assert int(resp.headers["x-total-count"]) == 1

    def test_search_filters_by_batch_id(self, client, session, open_batch):
        r1 = _errored_row(session, open_batch, source_row_number=1)
        r2 = _errored_row(session, open_batch, source_row_number=2)
        _discard(r1, session)
        _discard(r2, session)
        session.commit()

        resp = client.get(f"/api/staging/discarded?search={open_batch.id}")
        assert resp.status_code == 200
        assert int(resp.headers["x-total-count"]) == 2

    def test_search_no_match_returns_empty(self, client, session, open_batch):
        r = _errored_row(session, open_batch, source_row_number=1,
                         processing_error="real error")
        _discard(r, session)
        session.commit()

        resp = client.get("/api/staging/discarded?search=nonexistent_xyz")
        assert resp.status_code == 200
        assert resp.json() == []
        assert int(resp.headers["x-total-count"]) == 0
