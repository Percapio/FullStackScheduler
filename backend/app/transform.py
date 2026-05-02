from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Literal
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from .extractors import (
    JobDecomposition,
    decompose_job_string,
    extract_clear_date_from_notes,
    extract_ship_fields,
    extract_shipped_date,
)
from .models import (
    Assembly,
    Classification,
    Customer,
    ImportStagingRow,
    ImportStatus,
    Job,
    JobStatus,
    Salesperson,
)
from .sorting import resolve_ship_date


@dataclass(frozen=True)
class TransformOutcome:
    job: Job | None
    action: Literal["inserted", "updated", "errored"]


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


_PT = ZoneInfo("America/Los_Angeles")


def _today() -> date:
    return datetime.now(_PT).date()


def _mark_error(
    row: ImportStagingRow,
    message: str,
    *,
    suggestion: str | None = None,
) -> None:
    row.processing_status = ImportStatus.error
    row.processing_error = message
    row.suggested_correction = suggestion


def _upsert_customer(session: Session, name: str) -> Customer:
    customer = session.execute(
        select(Customer).where(Customer.name == name)
    ).scalar_one_or_none()
    if customer is None:
        customer = Customer(name=name)
        session.add(customer)
        session.flush()
    return customer


def _upsert_assembly(
    session: Session, decomp: JobDecomposition, row: ImportStagingRow,
) -> Assembly:
    assembly = session.execute(
        select(Assembly).where(Assembly.part_number == decomp.part_number)
    ).scalar_one_or_none()
    if assembly is None:
        assembly = Assembly(part_number=decomp.part_number)
        session.add(assembly)
        session.flush()

    if row.raw_mfg_notes is not None:
        assembly.base_mfg_notes = row.raw_mfg_notes
    if row.raw_prog is not None:
        assembly.program_name = row.raw_prog
    if row.raw_smt_plcmnts is not None:
        try:
            assembly.smt_placements = int(row.raw_smt_plcmnts)
        except (ValueError, TypeError):
            pass

    assembly.classifications.clear()
    for code in decomp.classification_codes:
        cls = _upsert_classification(session, code)
        assembly.classifications.append(cls)

    return assembly


def _upsert_classification(session: Session, code: str) -> Classification:
    cls = session.execute(
        select(Classification).where(Classification.code == code)
    ).scalar_one_or_none()
    if cls is None:
        cls = Classification(code=code)
        session.add(cls)
        session.flush()
    return cls


def _upsert_salesperson(session: Session, code: str) -> Salesperson:
    sp = session.execute(
        select(Salesperson).where(Salesperson.code == code)
    ).scalar_one_or_none()
    if sp is None:
        sp = Salesperson(code=code)
        session.add(sp)
        session.flush()
    return sp


def _apply_lines(row: ImportStagingRow, job: Job) -> None:
    job.line_1 = bool(row.raw_line_1 and row.raw_line_1.strip())
    job.line_2 = bool(row.raw_line_2 and row.raw_line_2.strip())
    job.line_3 = bool(row.raw_line_3 and row.raw_line_3.strip())


def _parse_int(raw: str | None) -> int | None:
    if not raw:
        return None
    token = raw.strip()
    if not token:
        return None
    try:
        return int(token)
    except ValueError:
        return None


def _parse_decimal(raw: str | None) -> Decimal | None:
    if not raw:
        return None
    cleaned = raw.strip().lstrip("$").replace(",", "").strip()
    if not cleaned:
        return None
    try:
        return Decimal(cleaned)
    except InvalidOperation:
        return None


def _resolve_clear_date(pcb_notes: str | None, kit_notes: str | None) -> str | None:
    pcb_date = extract_clear_date_from_notes(pcb_notes) if pcb_notes else None
    kit_date = extract_clear_date_from_notes(kit_notes) if kit_notes else None

    if pcb_date and kit_date:
        pcb_parts = [int(p) for p in pcb_date.split("/")[:2]]
        kit_parts = [int(p) for p in kit_date.split("/")[:2]]
        pcb_ordinal = pcb_parts[0] * 100 + pcb_parts[1]
        kit_ordinal = kit_parts[0] * 100 + kit_parts[1]
        return kit_date if kit_ordinal > pcb_ordinal else pcb_date

    return pcb_date or kit_date


def _apply_row_to_job(
    session: Session,
    row: ImportStagingRow,
    job: Job,
    *,
    decomp: JobDecomposition,
) -> bool:
    ship_date_text, lead_time = (
        extract_ship_fields(row.raw_ship_date) if row.raw_ship_date else (None, None)
    )
    job.ship_date_text = ship_date_text
    job.ship_lead_time_raw = lead_time
    job.notes_clear_date_raw = _resolve_clear_date(row.raw_pcb_notes, row.raw_kit_notes)
    job.run_pcb_notes = row.raw_pcb_notes
    job.kit_notes = row.raw_kit_notes
    job.scheduling_notes = row.raw_scheduling_notes
    job.ship_method = row.raw_ship_method
    job.bom_compare_photos = row.raw_bom_compare_photos
    job.smt_feeder_count = _parse_int(row.raw_smt_lines)
    job.run_cost = _parse_decimal(row.raw_code)

    if row.raw_doc_rel:
        job.doc_released_at = extract_shipped_date(row.raw_doc_rel)
    if row.raw_kit_rel:
        job.kit_released_at = extract_shipped_date(row.raw_kit_rel)

    if row.raw_sales_p and row.raw_sales_p.strip():
        job.salesperson_id = _upsert_salesperson(session, row.raw_sales_p.strip()).id

    if not _apply_shipped(row, job):
        return False

    job.resolved_ship_date = resolve_ship_date(
        ship_date_text=job.ship_date_text,
        status=job.status,
        shipped_at=job.shipped_at,
        today=_today(),
    )
    return True


def _apply_shipped(row: ImportStagingRow, job: Job) -> bool:
    """Apply the SHIPPED column to ``job``.

    Returns ``True`` on success (including the no-op blank-raw case), ``False``
    if an error was marked at source. On error, this function has already
    called :func:`_mark_error` with the full message and suggestion — callers
    MUST NOT mark the error again.
    """
    raw = row.raw_shipped
    if not raw or not raw.strip():
        return True
    job.status = JobStatus.shipped
    parsed = extract_shipped_date(raw)
    if parsed is not None:
        job.shipped_at = parsed
        return True
    _mark_error(
        row,
        f"Unparseable SHIPPED date: {raw!r}",
        suggestion="SHIPPED must be blank or a date like '2025-09-15' or '9/15/2025'.",
    )
    return False


def transform_staging_row(session: Session, row: ImportStagingRow) -> TransformOutcome:
    decomp = decompose_job_string(row.raw_job) if row.raw_job else None
    if decomp is None:
        _mark_error(
            row,
            f"Invalid JOB cell: {row.raw_job!r}",
            suggestion=(
                "JOB cell must contain a part number and a build type — "
                "e.g., '128764 NEW' or '128764\\nRONC 123456'."
            ),
        )
        return TransformOutcome(None, "errored")

    if decomp.split_suffix and len(decomp.split_suffix) > 32:
        _mark_error(
            row,
            f"split_suffix overflow ({len(decomp.split_suffix)} > 32): {decomp.split_suffix!r}",
            suggestion="Trim the split suffix (e.g., '-1par') to 32 characters or fewer.",
        )
        return TransformOutcome(None, "errored")
    if decomp.repeat_reference and len(decomp.repeat_reference) > 32:
        _mark_error(
            row,
            f"repeat_reference overflow ({len(decomp.repeat_reference)} > 32): {decomp.repeat_reference!r}",
            suggestion="Trim the repeat reference to 32 characters or fewer.",
        )
        return TransformOutcome(None, "errored")

    try:
        quantity = int(row.raw_qty)
    except (TypeError, ValueError):
        _mark_error(
            row,
            f"Invalid QTY: {row.raw_qty!r}",
            suggestion="QTY must be a positive whole number (e.g., '100').",
        )
        return TransformOutcome(None, "errored")
    if quantity < 1:
        _mark_error(
            row,
            f"Invalid QTY: {row.raw_qty!r}",
            suggestion="QTY must be a positive whole number (e.g., '100').",
        )
        return TransformOutcome(None, "errored")

    if not row.raw_customer or not row.raw_customer.strip():
        _mark_error(
            row,
            "raw_customer is empty",
            suggestion="Enter the customer name in the Customer column.",
        )
        return TransformOutcome(None, "errored")

    customer = _upsert_customer(session, row.raw_customer.strip())
    assembly = _upsert_assembly(session, decomp, row)
    session.flush()

    existing = session.execute(
        select(Job)
        .where(Job.assembly_id == assembly.id)
        .where(Job.build_type == decomp.build_type)
        .where(Job.split_suffix.is_not_distinct_from(decomp.split_suffix))
        .where(Job.repeat_reference.is_not_distinct_from(decomp.repeat_reference))
    ).scalar_one_or_none()

    if existing is None:
        job = Job(
            assembly_id=assembly.id,
            customer_id=customer.id,
            build_type=decomp.build_type,
            split_suffix=decomp.split_suffix,
            repeat_reference=decomp.repeat_reference,
            quantity=quantity,
        )
        session.add(job)
        action: Literal["inserted", "updated"] = "inserted"
    else:
        if existing.status is JobStatus.shipped:
            _mark_error(
                row,
                f"Conflict: Attempting to update a shipped job (job_id={existing.id})",
                suggestion=(
                    "Shipped jobs cannot be modified. If this is a new run of the same part, "
                    "add a split suffix to the JOB ID (e.g., add '-1par', '-2par', '-3par' as needed)."
                ),
            )
            return TransformOutcome(None, "errored")

        existing.customer_id = customer.id
        existing.quantity = quantity
        job = existing
        action = "updated"

    if not _apply_row_to_job(session, row, job, decomp=decomp):
        return TransformOutcome(None, "errored")

    if action == "inserted":
        session.flush()

    _apply_lines(row, job)

    row.processing_status = ImportStatus.processed
    session.flush()
    row.resolved_job_id = job.id
    row.processed_at = _now()

    return TransformOutcome(job, action)
