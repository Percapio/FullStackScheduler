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
