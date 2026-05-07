"""Contract tests for GET /api/staging/errored search and pagination (Phase 15 Epoch 1)."""
from __future__ import annotations

import pytest
from fastapi import status

from backend.app.models import ImportStagingRow, ImportStatus


def _errored_row(
    session,
    batch,
    source_row_number: int = 1,
    processing_error: str = "test error",
    suggested_correction: str | None = None,
    **overrides,
) -> ImportStagingRow:
    row = ImportStagingRow(
        batch_id=batch.id,
        source_row_number=source_row_number,
        processing_status=ImportStatus.error,
        processing_error=processing_error,
        suggested_correction=suggested_correction,
        **overrides,
    )
    session.add(row)
    session.flush()
    return row


class TestErroredSearch:
    def test_no_search_returns_all_errored_rows(self, client, session, open_batch):
        _errored_row(session, open_batch, source_row_number=1, processing_error="alpha error")
        _errored_row(session, open_batch, source_row_number=2, processing_error="beta error")

        resp = client.get("/api/staging/errored")
        assert resp.status_code == status.HTTP_200_OK
        assert int(resp.headers["x-total-count"]) == 2
        assert len(resp.json()) == 2

    def test_search_matches_processing_error(self, client, session, open_batch):
        _errored_row(session, open_batch, source_row_number=1, processing_error="cannot parse JOB")
        _errored_row(session, open_batch, source_row_number=2, processing_error="duplicate identity")

        resp = client.get("/api/staging/errored?search=parse")
        assert resp.status_code == status.HTTP_200_OK
        body = resp.json()
        assert int(resp.headers["x-total-count"]) == 1
        assert len(body) == 1
        assert body[0]["processing_error"] == "cannot parse JOB"

    def test_search_matches_suggested_correction(self, client, session, open_batch):
        _errored_row(
            session, open_batch, source_row_number=1,
            processing_error="err",
            suggested_correction="Try adding a split suffix",
        )
        _errored_row(
            session, open_batch, source_row_number=2,
            processing_error="err2",
            suggested_correction=None,
        )

        resp = client.get("/api/staging/errored?search=split")
        assert resp.status_code == status.HTTP_200_OK
        body = resp.json()
        assert int(resp.headers["x-total-count"]) == 1
        assert len(body) == 1
        assert body[0]["source_row_number"] == 1

    def test_search_matches_source_row_number_exact_integer(self, client, session, open_batch):
        r1 = _errored_row(session, open_batch, source_row_number=42, processing_error="err A")
        _errored_row(session, open_batch, source_row_number=99, processing_error="err B")

        resp = client.get("/api/staging/errored?search=42")
        assert resp.status_code == status.HTTP_200_OK
        body = resp.json()
        assert int(resp.headers["x-total-count"]) == 1
        assert body[0]["source_row_number"] == 42

    def test_search_matches_batch_id_exact_integer(self, client, session, open_batch):
        r1 = _errored_row(session, open_batch, source_row_number=1, processing_error="err")

        resp = client.get(f"/api/staging/errored?search={open_batch.id}")
        assert resp.status_code == status.HTTP_200_OK
        body = resp.json()
        assert int(resp.headers["x-total-count"]) == 1
        assert body[0]["batch_id"] == open_batch.id

    def test_search_non_numeric_does_not_match_integer_columns(self, client, session, open_batch):
        """Non-numeric search terms must NOT match source_row_number or batch_id."""
        _errored_row(session, open_batch, source_row_number=1, processing_error="zebra error")

        resp = client.get("/api/staging/errored?search=99abc")
        assert resp.status_code == status.HTTP_200_OK
        assert int(resp.headers["x-total-count"]) == 0

    def test_search_case_insensitive(self, client, session, open_batch):
        _errored_row(session, open_batch, source_row_number=1, processing_error="UPPERCASE ERROR TEXT")

        resp = client.get("/api/staging/errored?search=uppercase")
        assert resp.status_code == status.HTTP_200_OK
        assert int(resp.headers["x-total-count"]) == 1

    def test_x_total_count_reflects_full_match_set_not_page(self, client, session, open_batch):
        """x-total-count must equal all matches, not only the page returned."""
        for i in range(1, 8):
            _errored_row(session, open_batch, source_row_number=i, processing_error="needle in error")

        resp = client.get("/api/staging/errored?search=needle&limit=3&offset=0")
        assert resp.status_code == status.HTTP_200_OK
        assert int(resp.headers["x-total-count"]) == 7
        assert len(resp.json()) == 3

    def test_empty_search_returns_all(self, client, session, open_batch):
        _errored_row(session, open_batch, source_row_number=1, processing_error="anything")
        _errored_row(session, open_batch, source_row_number=2, processing_error="more")

        resp = client.get("/api/staging/errored?search=")
        assert resp.status_code == status.HTTP_200_OK
        assert int(resp.headers["x-total-count"]) == 2

    def test_search_no_match_returns_empty(self, client, session, open_batch):
        _errored_row(session, open_batch, source_row_number=1, processing_error="some error")

        resp = client.get("/api/staging/errored?search=xyzzy_no_match")
        assert resp.status_code == status.HTTP_200_OK
        assert resp.json() == []
        assert int(resp.headers["x-total-count"]) == 0

    def test_search_excludes_discarded_rows(self, client, session, open_batch):
        """Discarded rows must not appear in errored search results."""
        from datetime import UTC, datetime
        r = _errored_row(session, open_batch, source_row_number=1, processing_error="visible error")
        discarded = _errored_row(session, open_batch, source_row_number=2, processing_error="visible error")
        discarded.discarded_at = datetime.now(UTC)
        session.flush()

        resp = client.get("/api/staging/errored?search=visible")
        assert resp.status_code == status.HTTP_200_OK
        body = resp.json()
        assert int(resp.headers["x-total-count"]) == 1
        assert body[0]["id"] == r.id

    def test_search_excludes_duplicate_group_rows(self, client, session, open_batch):
        """Rows in a duplicate group must not appear in errored search results."""
        _errored_row(
            session, open_batch, source_row_number=1,
            processing_error="group error",
            duplicate_group_key="PN|TYPE|||}",
        )
        r = _errored_row(
            session, open_batch, source_row_number=2,
            processing_error="group error",
        )

        resp = client.get("/api/staging/errored?search=group")
        assert resp.status_code == status.HTTP_200_OK
        body = resp.json()
        assert int(resp.headers["x-total-count"]) == 1
        assert body[0]["id"] == r.id


class TestErroredPagination:
    def test_default_limit_is_50(self, client, session, open_batch):
        """New default is 50 (changed from 100 in Epoch 1)."""
        for i in range(1, 52):
            _errored_row(session, open_batch, source_row_number=i)
        session.commit()

        resp = client.get("/api/staging/errored")
        assert resp.status_code == status.HTTP_200_OK
        assert len(resp.json()) == 50
        assert int(resp.headers["x-total-count"]) == 51

    def test_offset_pages_correctly(self, client, session, open_batch):
        for i in range(1, 6):
            _errored_row(session, open_batch, source_row_number=i)
        session.commit()

        page0 = client.get("/api/staging/errored?limit=2&offset=0")
        page1 = client.get("/api/staging/errored?limit=2&offset=2")
        page2 = client.get("/api/staging/errored?limit=2&offset=4")

        assert int(page0.headers["x-total-count"]) == 5
        assert len(page0.json()) == 2
        assert len(page1.json()) == 2
        assert len(page2.json()) == 1

    def test_negative_limit_returns_422(self, client):
        resp = client.get("/api/staging/errored?limit=-1")
        assert resp.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY

    def test_negative_offset_returns_422(self, client):
        resp = client.get("/api/staging/errored?offset=-1")
        assert resp.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY
