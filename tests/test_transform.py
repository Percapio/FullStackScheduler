from __future__ import annotations

from datetime import date
from unittest.mock import patch

import pytest
from sqlalchemy.orm import Session

from backend.app.models import (
    ImportBatch,
    ImportStagingRow,
    ImportStatus,
    Job,
    JobStatus,
)
from backend.app.transform import transform_staging_row


def _seed_staging_row(session: Session, batch_id: int, **overrides) -> ImportStagingRow:
    defaults = dict(
        batch_id=batch_id,
        source_row_number=1,
        processing_status=ImportStatus.pending,
        raw_job="128764\nNEW",
        raw_qty="100",
        raw_customer="ACME Corp",
        raw_ship_date="9/15 30D",
    )
    defaults.update(overrides)
    row = ImportStagingRow(**defaults)
    session.add(row)
    session.flush()
    return row


class TestResolvedShipDateOnInsert:
    def test_transform_sets_resolved_ship_date_on_insert(self, session, engine):
        batch = ImportBatch(source_file="test.xlsx")
        session.add(batch)
        session.flush()

        row = _seed_staging_row(session, batch.id)

        with patch("backend.app.transform._today", return_value=date(2026, 4, 19)):
            outcome = transform_staging_row(session, row)

        assert outcome.action == "inserted"
        job = session.get(Job, outcome.job.id)
        assert job.resolved_ship_date is not None
        assert job.resolved_ship_date == date(2026, 9, 15)


class TestResolvedShipDateOnUpdate:
    def test_transform_recomputes_resolved_ship_date_on_update(self, session, engine):
        batch = ImportBatch(source_file="test.xlsx")
        session.add(batch)
        session.flush()

        row1 = _seed_staging_row(session, batch.id, source_row_number=1)
        with patch("backend.app.transform._today", return_value=date(2025, 12, 1)):
            outcome1 = transform_staging_row(session, row1)
        assert outcome1.action == "inserted"
        job = session.get(Job, outcome1.job.id)
        assert job.resolved_ship_date == date(2025, 9, 15)

        row2 = _seed_staging_row(session, batch.id, source_row_number=2)
        with patch("backend.app.transform._today", return_value=date(2026, 1, 5)):
            outcome2 = transform_staging_row(session, row2)
        assert outcome2.action == "updated"
        session.expire_all()
        job = session.get(Job, outcome2.job.id)
        assert job.resolved_ship_date == date(2026, 9, 15)


class TestApplyShippedRegression:
    def test_apply_shipped_marks_error_with_unchanged_message(self, session, engine):
        """Regression: F9 refactor preserves the SHIPPED-error literal verbatim."""
        batch = ImportBatch(source_file="test.xlsx")
        session.add(batch)
        session.flush()

        row = _seed_staging_row(session, batch.id, raw_shipped="not-a-date")
        outcome = transform_staging_row(session, row)

        assert outcome.action == "errored"
        assert row.processing_status == ImportStatus.error
        assert row.processing_error == "Unparseable SHIPPED date: 'not-a-date'"
        assert row.suggested_correction is not None
        assert row.suggested_correction.startswith("SHIPPED must be blank")


class TestSentinelShipDate:
    def test_transform_persists_sentinel_ship_date(self, session, engine):
        batch = ImportBatch(source_file="test.xlsx")
        session.add(batch)
        session.flush()

        row = _seed_staging_row(session, batch.id, raw_ship_date="???")

        with patch("backend.app.transform._today", return_value=date(2026, 4, 19)):
            outcome = transform_staging_row(session, row)

        assert outcome.action == "inserted"
        job = session.get(Job, outcome.job.id)
        assert job.ship_date_text == "???"
        assert job.resolved_ship_date is None
