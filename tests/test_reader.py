from openpyxl.cell.rich_text import CellRichText, TextBlock
from openpyxl.cell.text import InlineFont

from backend.app.reader import cell_to_markdown, cell_to_text


def test_cell_to_text_strips_rich_text_formatting():
    cell = CellRichText([
        TextBlock(InlineFont(b=True), "139238"),
    ])
    assert cell_to_text(cell) == "139238"


def test_cell_to_text_handles_plain_string():
    assert cell_to_text("unformatted") == "unformatted"


def test_cell_to_text_handles_none():
    assert cell_to_text(None) is None


def test_cell_to_markdown_preserves_bold_italic_and_strikethrough():
    cell = CellRichText([
        TextBlock(InlineFont(b=True), "warning"),
        ": ",
        TextBlock(InlineFont(i=True), "inspect"),
        " before ",
        TextBlock(InlineFont(strike=True), "removal"),
    ])

    assert cell_to_markdown(cell) == "**warning**: *inspect* before ~~removal~~"


def test_cell_to_markdown_handles_plain_string():
    assert cell_to_markdown("unformatted") == "unformatted"


def test_cell_to_markdown_handles_none():
    assert cell_to_markdown(None) is None


# ---------------------------------------------------------------------------
# Header normalization (Phase 22 Part 1) — pure functions
# ---------------------------------------------------------------------------

from datetime import datetime as _dt

import pytest

from backend.app.reader import (
    KNOWN_HEADERS,
    HeaderCollision,
    SheetLayout,
    assert_constants_are_normal,
    normalize_header,
    reconcile_headers,
    resolve_column_indices,
)


def test_normalize_header_strips_trailing_space():
    assert normalize_header("KIT REL ") == "KIT REL"


def test_normalize_header_strips_leading_space():
    assert normalize_header("  JOB") == "JOB"


def test_normalize_header_passes_clean_header_through():
    assert normalize_header("BOM COMPARE / PHOTOS") == "BOM COMPARE / PHOTOS"


def test_normalize_header_does_not_case_fold():
    """Strip only. 'Kit Rel' still drops — deliberately, and visibly (§1.1)."""
    assert normalize_header("Kit Rel") == "Kit Rel"
    assert normalize_header("Kit Rel") not in KNOWN_HEADERS


def test_normalize_header_does_not_collapse_internal_whitespace():
    assert normalize_header("BOM  COMPARE / PHOTOS") == "BOM  COMPARE / PHOTOS"


def test_normalize_header_none_returns_none():
    assert normalize_header(None) is None


def test_normalize_header_whitespace_only_returns_none():
    assert normalize_header("   ") is None
    assert normalize_header("") is None


def test_normalize_header_int_returns_none():
    assert normalize_header(4) is None


def test_normalize_header_datetime_returns_none():
    assert normalize_header(_dt(2026, 8, 28)) is None


# ---- resolve_column_indices ---------------------------------------------------


def test_resolve_column_indices_binds_known_headers_to_position():
    headers = ["JOB", None, "QTY", "FEEDER S/U"]
    assert resolve_column_indices(headers, None) == {"JOB": 0, "QTY": 2}


def test_resolve_column_indices_first_occurrence_wins():
    headers = ["KIT REL", "JOB", "KIT REL"]
    assert resolve_column_indices(headers, None)["KIT REL"] == 0


def test_resolve_column_indices_excludes_divider_sentinel():
    headers = ["JOB", "DIVIDER"]
    assert "DIVIDER" not in resolve_column_indices(headers, "DIVIDER")


def test_resolve_column_indices_preserves_sheet_order():
    headers = ["QTY", "JOB", "CUSTOMER"]
    assert list(resolve_column_indices(headers, None)) == ["QTY", "JOB", "CUSTOMER"]


# ---- reconcile_headers --------------------------------------------------------


def _all_known_in_order() -> list[str | None]:
    return sorted(KNOWN_HEADERS)


def test_reconcile_headers_partitions_known_headers():
    reconciliation = reconcile_headers(_all_known_in_order(), None, "SCHD")
    assert reconciliation.matched | reconciliation.missing == KNOWN_HEADERS
    assert reconciliation.matched & reconciliation.missing == frozenset()


def test_reconcile_headers_reports_missing_column():
    headers = [h for h in sorted(KNOWN_HEADERS) if h != "KIT REL"]
    reconciliation = reconcile_headers(headers, None, "SCHD")
    assert reconciliation.missing == frozenset({"KIT REL"})


def test_reconcile_headers_unrecognised_preserves_sheet_order():
    headers = ["FEEDER S/U", "JOB", "STENCIL"]
    reconciliation = reconcile_headers(headers, None, "SCHD")
    assert reconciliation.unrecognised == ("FEEDER S/U", "STENCIL")


def test_reconcile_headers_excludes_divider_from_unrecognised():
    reconciliation = reconcile_headers(["JOB", "DIVIDER"], "DIVIDER", "SCHD")
    assert "DIVIDER" not in reconciliation.unrecognised


def test_reconcile_headers_excludes_none_cells_from_unrecognised():
    reconciliation = reconcile_headers(["JOB", None, None], None, "SCHD")
    assert reconciliation.unrecognised == ()


def test_reconcile_headers_two_extra_occurrences_yield_two_collisions():
    headers = ["KIT REL", "KIT REL", "KIT REL"]
    reconciliation = reconcile_headers(headers, None, "SCHD")
    assert reconciliation.collisions == (
        HeaderCollision(header="KIT REL", kept_index=0, ignored_index=1),
        HeaderCollision(header="KIT REL", kept_index=0, ignored_index=2),
    )


def test_reconcile_headers_is_pure():
    headers = ["JOB", "KIT REL"]
    before = list(headers)
    reconcile_headers(headers, None, "SCHD")
    assert headers == before


# ---- constant drift guards ----------------------------------------------------


def test_assert_constants_are_normal_passes_for_live_constants():
    from backend.app.ingest import _COLUMN_MAP

    assert_constants_are_normal(_COLUMN_MAP.keys())


def test_assert_constants_are_normal_rejects_denormalized_known_header(monkeypatch):
    import backend.app.reader as reader_module

    monkeypatch.setattr(
        reader_module, "KNOWN_HEADERS", frozenset({"JOB", "KIT REL "})
    )
    with pytest.raises(AssertionError):
        reader_module.assert_constants_are_normal()


def test_assert_constants_are_normal_rejects_denormalized_column_map_key():
    with pytest.raises(AssertionError):
        assert_constants_are_normal(["KIT REL "])


def test_sheet_layout_rejects_denormalized_divider_at_construction():
    """Per-instance, not a walk over _LAYOUTS — an unregistered layout cannot escape."""
    with pytest.raises(AssertionError):
        SheetLayout(header_row=1, divider_header="DIVIDER ")


def test_sheet_layout_accepts_normal_divider():
    assert SheetLayout(header_row=1, divider_header="DIVIDER").divider_header == "DIVIDER"


def test_sheet_layout_accepts_none_divider():
    assert SheetLayout(header_row=1, divider_header=None).divider_header is None
