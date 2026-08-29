from collections.abc import Iterator
import csv
import enum
import io
import re
from typing import NamedTuple, Callable

from ..models import Job
from ..config import get_settings

class HistoryExportColumn(NamedTuple):
    key: str
    header: str
    render: Callable[[Job], str]

class DelimiterToken(enum.Enum):
    comma = "comma"
    tab = "tab"
    semicolon = "semicolon"
    pipe = "pipe"

DELIMITER_CHARACTERS = {
    DelimiterToken.comma: ",",
    DelimiterToken.tab: "\t",
    DelimiterToken.semicolon: ";",
    DelimiterToken.pipe: "|",
}

def flatten_operator_notes(raw_notes: str | None) -> str:
    if raw_notes is None:
        return ""
    # 1. Remove paired ~~...~~ non-greedily
    text = re.sub(r'~~[\s\S]*?~~', '', raw_notes)
    # 2. Unpaired ~~ survives as literal text
    # 3. Strip ** and *
    text = text.replace('**', '').replace('*', '')
    # 4. Lines trimmed, empty lines dropped, joined with " | "
    lines = [line.strip() for line in text.splitlines()]
    survivors = [line for line in lines if line]
    return " | ".join(survivors)

def neutralise_formula_prefix(cell_text: str) -> str:
    if not cell_text:
        return cell_text
    first = cell_text[0]
    if first in ('=', '+', '-', '@', '\t', '\r'):
        return f"'{cell_text}"
    return cell_text

def _render_job(job: Job) -> str:
    part_number = job.assembly.part_number if job.assembly else ""
    suffixes = []
    if job.split_suffix:
        suffixes.append(job.split_suffix)
    if job.repeat_reference:
        suffixes.append(f"RONC {job.repeat_reference}")
    if suffixes:
        suffix_str = " · ".join(suffixes)
        return f"{part_number} · {suffix_str}"
    return part_number

def _render_build_type(job: Job) -> str:
    if not job.build_type or job.build_type.value == "new":
        return ""
    return job.build_type.value.upper()

HISTORY_EXPORT_COLUMNS: tuple[HistoryExportColumn, ...] = (
    HistoryExportColumn(
        key="ship_date",
        header="Ship Date",
        render=lambda job: job.shipped_at.isoformat() if job.shipped_at else "",
    ),
    HistoryExportColumn(
        key="job",
        header="Job",
        render=_render_job,
    ),
    HistoryExportColumn(
        key="quantity",
        header="Qty",
        render=lambda job: str(job.quantity) if job.quantity is not None else "",
    ),
    HistoryExportColumn(
        key="build_type",
        header="ROWC/RONC",
        render=_render_build_type,
    ),
    HistoryExportColumn(
        key="mfg_notes",
        header="Mfg Notes",
        render=lambda job: flatten_operator_notes(job.assembly.base_mfg_notes) if job.assembly else "",
    ),
    HistoryExportColumn(
        key="customer",
        header="Customer",
        render=lambda job: job.customer.name if job.customer else "",
    ),
)

HISTORY_EXPORT_COLUMNS_BY_KEY = {col.key: col for col in HISTORY_EXPORT_COLUMNS}

def generate_csv_rows(
    job_iterator: Iterator[Job],
    columns: list[HistoryExportColumn],
    delimiter: str
) -> Iterator[str]:
    # Byte Order Mark for Excel UTF-8
    yield "\ufeff"
    
    output = io.StringIO()
    writer = csv.writer(output, delimiter=delimiter, lineterminator="\r\n")
    
    # Write header
    writer.writerow([c.header for c in columns])
    yield output.getvalue()
    output.truncate(0)
    output.seek(0)
    
    # Write rows
    for job in job_iterator:
        row = [neutralise_formula_prefix(c.render(job)) for c in columns]
        writer.writerow(row)
        yield output.getvalue()
        output.truncate(0)
        output.seek(0)
