from __future__ import annotations

from collections.abc import Iterator
from datetime import date, datetime

from openpyxl import load_workbook
from openpyxl.cell.rich_text import CellRichText

SHEET_NAME = "SHIPPED (AA)"

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
    wb = load_workbook(path, rich_text=True, data_only=True)
    ws = wb[sheet]
    headers = [c.value for c in next(ws.iter_rows(min_row=1, max_row=1))]
    for row_number, row in enumerate(
        ws.iter_rows(min_row=2, values_only=False), start=2
    ):
        cells: dict[str, str | None] = {}
        for i, cell in enumerate(row):
            if i >= len(headers):
                break
            header = headers[i]
            if header not in KNOWN_HEADERS:
                continue
            extract = cell_to_markdown if header in MARKDOWN_HEADERS else cell_to_text
            cells[header] = extract(cell.value)
        if all(v is None or v == "" for v in cells.values()):
            continue
        yield row_number, cells
