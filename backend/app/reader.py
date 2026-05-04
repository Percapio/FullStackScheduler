from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from datetime import date, datetime

from openpyxl import load_workbook
from openpyxl.cell.rich_text import CellRichText

PRIMARY_SHEET_NAME: str = "SCHD"
FALLBACK_SHEET_NAME: str = "SHIPPED (AA)"

# Stage 3 flip: SCHD is the new default. resolve_sheet falls back to
# FALLBACK_SHEET_NAME when PRIMARY_SHEET_NAME is requested but absent.
SHEET_NAME: str = PRIMARY_SHEET_NAME

KNOWN_HEADERS: frozenset[str] = frozenset({
    "SHIPPED", "PCB NOTES", "KIT NOTES", "SCHEDULING NOTES",
    "LINE 1", "LINE 2", "LINE 3", "JOB", "QTY", "SHIP DATE",
    "PROG", "MFG NOTES", "SMT LINES", "SMT PLCMNTS",
    "SHIP METHOD", "CUSTOMER", "SALES P", "DOC REL", "KIT REL",
    "CODE", "BOM COMPARE / PHOTOS",
})

MARKDOWN_HEADERS: frozenset[str] = frozenset({
    "MFG NOTES",
    "PCB NOTES",
    "SCHEDULING NOTES",
    "KIT NOTES",
})

_BOLD = "**"
_ITALIC = "*"
_STRIKE = "~~"


# ---------------------------------------------------------------------------
# Sheet layout primitives (Stage 1)
# ---------------------------------------------------------------------------


class MissingDividerColumnError(Exception):
    """Raised when a SheetLayout declares divider_header but the resolved
    sheet's header row does not contain that column.

    Pre:  sheet_name is the actual sheet selected by resolve_sheet.
          expected_header is the layout's divider_header.
    Post: instance carries both for error formatting; no further processing.
    """

    def __init__(self, sheet_name: str, expected_header: str) -> None:
        self.sheet_name = sheet_name
        self.expected_header = expected_header
        super().__init__(
            f"Sheet {sheet_name!r} is missing required column "
            f"{expected_header!r}; cannot detect section dividers."
        )


class EmptySheetError(Exception):
    """Raised when the resolved sheet has fewer rows than layout.header_row.

    Pre:  sheet_name is the resolved sheet. expected_header_row is
          layout.header_row. actual_max_row is ws.max_row at validation time.
    Post: instance carries all three for diagnostics.
    """

    def __init__(
        self, sheet_name: str, expected_header_row: int, actual_max_row: int
    ) -> None:
        self.sheet_name = sheet_name
        self.expected_header_row = expected_header_row
        self.actual_max_row = actual_max_row
        super().__init__(
            f"Sheet {sheet_name!r} has {actual_max_row} rows; "
            f"layout requires header at row {expected_header_row}."
        )


@dataclass(frozen=True)
class SheetLayout:
    """Per-sheet structural rules consumed by read_rows.

    Pre:  header_row is 1-based and points at the row containing column
          names. divider_header, when set, names a column whose non-empty
          cell marks the row as a section-divider header to be skipped.
    Post: instances are immutable; equality is structural.

    Note: the sheet's name is the dict key in _LAYOUTS, not a field on
    this dataclass — there is exactly one source of truth for "what is
    this layout called."
    """

    header_row: int
    divider_header: str | None  # None = no divider filtering for this shape


_LAYOUTS: dict[str, SheetLayout] = {
    "SCHD": SheetLayout(header_row=2, divider_header="DIVIDER"),
    "SHIPPED (AA)": SheetLayout(header_row=1, divider_header=None),
}


def resolve_sheet(wb, requested: str) -> str:
    """Return the actual sheet name to load from *wb*.

    Pre:  wb is an opened openpyxl Workbook. requested is the caller's
          sheet name.
    Post: returns requested verbatim when present in wb.sheetnames.
          Falls back to FALLBACK_SHEET_NAME only when requested equals
          PRIMARY_SHEET_NAME and FALLBACK_SHEET_NAME is present. All
          other absent requests raise KeyError — no silent re-routing.
    Raises: KeyError when no acceptable sheet is found.
    """
    if requested in wb.sheetnames:
        return requested
    if requested == PRIMARY_SHEET_NAME and FALLBACK_SHEET_NAME in wb.sheetnames:
        return FALLBACK_SHEET_NAME
    raise KeyError(requested)


def resolve_layout(sheet_name: str) -> SheetLayout:
    """Return the registered SheetLayout for *sheet_name*.

    Pre:  sheet_name is the actual sheet name returned by resolve_sheet.
    Post: returns the registered SheetLayout.
    Raises: KeyError when no layout is registered for sheet_name — protects
            against silent shape drift when a new sheet is wired through
            resolve_sheet without a matching layout entry.
    """
    return _LAYOUTS[sheet_name]


def cell_to_text(value) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, CellRichText):
        return "".join(getattr(run, "text", None) or str(run) for run in value)
    return str(value)


def cell_to_markdown(value) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if not isinstance(value, CellRichText):
        return str(value)
    parts: list[str] = []
    for run in value:
        text = getattr(run, "text", None) or str(run)
        font = getattr(run, "font", None)
        if font is not None:
            if getattr(font, "strike", False):
                text = f"{_STRIKE}{text}{_STRIKE}"
            if getattr(font, "i", False):
                text = f"{_ITALIC}{text}{_ITALIC}"
            if getattr(font, "b", False):
                text = f"{_BOLD}{text}{_BOLD}"
        parts.append(text)
    return "".join(parts)


def read_rows(
    path: str, sheet: str = SHEET_NAME
) -> Iterator[tuple[int, dict[str, str | None]]]:
    """Return an iterator over (row_number, cells) for every data row.

    Pre:  path is a readable .xlsx; sheet is either the default
          (SHEET_NAME == PRIMARY_SHEET_NAME) or an explicit name.
    Post: yields (row_number, cells) for each non-divider, non-empty row.
          Title rows (above layout.header_row) are skipped. Section-divider
          rows are skipped when the layout declares a divider_header.
          All-empty rows are skipped. DIVIDER is never included in cells.
    Raises (on CALL, not on first next()):
          KeyError                  — no acceptable sheet found.
          MissingDividerColumnError — layout requires DIVIDER column not present.
          EmptySheetError           — sheet has fewer rows than layout.header_row.
    Propagates: openpyxl load errors (FileNotFoundError, BadZipFile, ...).
    """
    wb = load_workbook(path, rich_text=True, data_only=True)
    sheet_name = resolve_sheet(wb, sheet)
    layout = resolve_layout(sheet_name)
    ws = wb[sheet_name]

    if ws.max_row is None or ws.max_row < layout.header_row:
        actual = ws.max_row or 0
        raise EmptySheetError(sheet_name, layout.header_row, actual)

    header_cells = next(
        ws.iter_rows(min_row=layout.header_row, max_row=layout.header_row)
    )
    headers = [c.value for c in header_cells]
    divider_index = _resolve_divider_index(layout, headers, sheet_name)

    return _iter_data_rows(ws, headers, layout, divider_index)


def _resolve_divider_index(
    layout: SheetLayout, headers: list, sheet_name: str
) -> int | None:
    """Return the 0-based column index of the divider column, or None.

    Pre:  headers is the list of header values from the resolved sheet
          (may contain None entries for blank columns).
    Post: returns the index when the layout requires divider filtering and
          the column is present. Returns None when the layout does not
          declare divider filtering.
    Raises: MissingDividerColumnError when the layout declares a divider
            header but headers does not contain it.
    """
    if layout.divider_header is None:
        return None
    try:
        return headers.index(layout.divider_header)
    except ValueError:
        raise MissingDividerColumnError(sheet_name, layout.divider_header)


def _iter_data_rows(
    ws, headers: list, layout: SheetLayout, divider_index: int | None
) -> Iterator[tuple[int, dict[str, str | None]]]:
    """Yield (row_number, cells) for each non-divider, non-empty data row.

    Pre:  ws is the resolved openpyxl Worksheet. headers is the list of
          header values (may contain None). layout is the resolved SheetLayout.
          divider_index is None or a valid 0-based index into headers.
    Post: yields (row_number, cells) per row. Divider rows and all-empty
          rows are skipped.
    """
    for row_number, row in enumerate(
        ws.iter_rows(min_row=layout.header_row + 1, values_only=False),
        start=layout.header_row + 1,
    ):
        if _is_divider_row(row, divider_index):
            continue
        cells: dict[str, str | None] = {}
        for i, cell in enumerate(row):
            if i >= len(headers):
                break
            header = headers[i]
            if header is None or header not in KNOWN_HEADERS:
                continue
            extract = cell_to_markdown if header in MARKDOWN_HEADERS else cell_to_text
            cells[header] = extract(cell.value)
        if all(v is None or v == "" for v in cells.values()):
            continue
        yield row_number, cells


def _is_divider_row(row, divider_index: int | None) -> bool:
    """Return True iff the row is a section-divider row.

    Pre:  row is a tuple of openpyxl Cell objects. divider_index is None or
          a valid 0-based column index.
    Post: True iff divider_index is not None AND the cell at that index
          exists AND the value satisfies Decision 5's non-empty rule:
            - None                          -> False
            - str, stripped == ""           -> False (whitespace-only typo)
            - str, stripped != ""           -> True
            - any non-None non-string value -> True
    """
    if divider_index is None:
        return False
    if divider_index >= len(row):
        return False
    value = row[divider_index].value
    if value is None:
        return False
    if isinstance(value, str):
        return value.strip() != ""
    return True

