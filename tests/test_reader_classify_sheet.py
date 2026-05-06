"""classify_sheet — pure mapping tests (Epoch 2).

Verifies that every registered sheet name maps to the correct SheetKind
and that an unregistered name raises KeyError.
"""
import pytest

from backend.app.models import SheetKind
from backend.app.reader import PRIMARY_SHEET_NAME, FALLBACK_SHEET_NAME, classify_sheet


def test_classify_sheet_primary_returns_live():
    assert classify_sheet(PRIMARY_SHEET_NAME) is SheetKind.live


def test_classify_sheet_fallback_returns_historical():
    assert classify_sheet(FALLBACK_SHEET_NAME) is SheetKind.historical


def test_classify_sheet_unknown_raises_key_error():
    with pytest.raises(KeyError):
        classify_sheet("UNKNOWN SHEET")


def test_classify_sheet_empty_string_raises_key_error():
    with pytest.raises(KeyError):
        classify_sheet("")
