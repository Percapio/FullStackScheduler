"""ImportBatch.sheet_kind persistence — Epoch 2.

Verifies that ingest_workbook populates ImportBatch.sheet_kind correctly
for both workbook flavours, including the SCHD-requested → SHIPPED-fallback
resolution path.
"""
from sqlalchemy import select

from backend.app.ingest import ingest_workbook
from backend.app.models import ImportBatch, SheetKind


def test_schd_workbook_sets_live_sheet_kind(schd_workbook_factory, session_factory):
    path = schd_workbook_factory(
        [{"data": {"JOB": "128764\nNEW", "QTY": "5", "CUSTOMER": "ACME"}}]
    )

    result = ingest_workbook(path, session_factory=session_factory)

    assert result.sheet_kind is SheetKind.live

    with session_factory() as s:
        batch = s.get(ImportBatch, result.batch_id)
        assert batch.sheet_kind is SheetKind.live


def test_shipped_aa_workbook_sets_historical_sheet_kind(workbook_factory, session_factory):
    path = workbook_factory(
        [{"JOB": "128764\nNEW", "QTY": "5", "CUSTOMER": "ACME", "SHIPPED": "04/01/2026"}]
    )

    result = ingest_workbook(path, sheet="SHIPPED (AA)", session_factory=session_factory)

    assert result.sheet_kind is SheetKind.historical

    with session_factory() as s:
        batch = s.get(ImportBatch, result.batch_id)
        assert batch.sheet_kind is SheetKind.historical


def test_schd_requested_but_shipped_aa_present_sets_historical(workbook_factory, session_factory):
    """SCHD requested but only SHIPPED (AA) exists → resolve_sheet falls back;
    sheet_kind must reflect the *resolved* sheet, not the requested one.
    """
    # workbook_factory produces a workbook with only the "SHIPPED (AA)" sheet.
    path = workbook_factory(
        [{"JOB": "128764\nNEW", "QTY": "5", "CUSTOMER": "ACME", "SHIPPED": "04/01/2026"}]
    )

    # Default sheet argument is SCHD; resolve_sheet will fall back to SHIPPED (AA).
    result = ingest_workbook(path, session_factory=session_factory)

    assert result.sheet_kind is SheetKind.historical

    with session_factory() as s:
        batch = s.get(ImportBatch, result.batch_id)
        assert batch.sheet_kind is SheetKind.historical
