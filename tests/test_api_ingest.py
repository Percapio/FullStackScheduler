"""Contract tests for /api/ingest (Phase 09)."""
from __future__ import annotations

import io
from unittest.mock import patch

import pytest
from fastapi import status

from backend.app.ingest import DuplicateBatchError, IngestResult
from backend.app.models import SheetKind


_FAKE_RESULT = IngestResult.processed_or_error(
    batch_id=42,
    source_sha256="a" * 64,
    filename="schedule.xlsx",
    rows_total=10,
    rows_inserted=7,
    rows_updated=2,
    rows_errored=1,
    duplicate_of_batch_id=None,
    sheet_kind=SheetKind.live,
)


def _payload(filename: str = "schedule.xlsx", body: bytes = b"PK\x03\x04stub") -> dict:
    return {"file": (filename, io.BytesIO(body), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")}


class TestIngestUpload:
    def test_rejects_non_xlsx_suffix(self, client):
        resp = client.post("/api/ingest", files=_payload("schedule.csv"))
        assert resp.status_code == status.HTTP_400_BAD_REQUEST
        assert ".xlsx" in resp.json()["detail"]

    def test_success_returns_row_counts(self, client):
        with patch("backend.app.api.ingest.ingest_workbook", return_value=_FAKE_RESULT) as m:
            resp = client.post("/api/ingest", files=_payload())
        assert resp.status_code == status.HTTP_200_OK
        body = resp.json()
        assert body["batch_id"] == 42
        assert body["rows_total"] == 10
        assert body["rows_inserted"] == 7
        assert body["rows_updated"] == 2
        assert body["rows_errored"] == 1
        assert body["filename"] == "schedule.xlsx"
        # Pipeline was called exactly once with sheet defaulted, force=False.
        assert m.call_count == 1
        _, kwargs = m.call_args
        assert kwargs["force"] is False

    def test_duplicate_returns_500_with_detail(self, client):
        exc = DuplicateBatchError(existing_batch_id=11, source_sha256="b" * 64)
        with patch("backend.app.api.ingest.ingest_workbook", side_effect=exc):
            resp = client.post("/api/ingest", files=_payload())
        assert resp.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR
        assert "11" in resp.json()["detail"]

    def test_unexpected_exception_returns_500_with_message(self, client):
        with patch("backend.app.api.ingest.ingest_workbook",
                   side_effect=RuntimeError("kaboom")):
            resp = client.post("/api/ingest", files=_payload())
        assert resp.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR
        assert "kaboom" in resp.json()["detail"]

    def test_force_flag_is_forwarded(self, client):
        with patch("backend.app.api.ingest.ingest_workbook", return_value=_FAKE_RESULT) as m:
            resp = client.post("/api/ingest?force=true", files=_payload())
        assert resp.status_code == status.HTTP_200_OK
        _, kwargs = m.call_args
        assert kwargs["force"] is True
