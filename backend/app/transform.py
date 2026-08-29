from __future__ import annotations

import enum
import logging
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Literal
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from .extractors import (
    DecomposeError,
    JobDecomposition,
    decompose_job_string_with_diagnostic,
    extract_clear_date_from_notes,
    extract_ship_fields,
    extract_shipped_date,
)
from .config import get_settings
from .models import (
    Assembly,
    BuildQualifier,
    Classification,
    Customer,
    ImportStagingRow,
    ImportStatus,
    Job,
    JobSecondOpsLine,
    JobStatus,
    Salesperson,
    SheetKind,
)
from .services.upserts import upsert_customer as _upsert_customer
from .sorting import resolve_ship_date

log = logging.getLogger(__name__)


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


def _build_r1_suggestion(err: DecomposeError | None) -> str:
    """Return the R1 suggestion text, appending recovered classification codes
    when the parser detected any in the failing cell.

    Pre:  err is None or a DecomposeError with code == 'R1_no_classifier'.
    Post: base suggestion always returned; recovered codes appended when present.
    Raises: never.
    """
    base = (
        "JOB cell must contain a part number and a build type — "
        "e.g., '128764 NEW' or '128764\\nRONC 123456'."
    )
    if err and err.recovered_classifications:
        codes = ", ".join(err.recovered_classifications)
        return base + f" Detected classification codes ({codes}) will be preserved on retry."
    return base


def _mark_decompose_error(row: ImportStagingRow, err: DecomposeError | None) -> None:
    """Mark row as errored with a code-specific message and suggestion.

    Pre:  err is None (raw_job was empty/None) or a DecomposeError.
    Post: row.processing_status, row.processing_error, and
          row.suggested_correction are all set.  No other mutations.
    Raises: never.
    """
    if err is None or err.code == "R1_no_classifier":
        _mark_error(
            row,
            f"Invalid JOB cell: {row.raw_job!r}",
            suggestion=_build_r1_suggestion(err),
        )
    elif err.code == "R2_multiple_qualifiers":
        _mark_error(
            row,
            f"Multiple build qualifiers in JOB cell: {row.raw_job!r}",
            suggestion="JOB cell may contain at most one of RWK, REWORK, or RMA. Remove the redundant token.",
        )
    elif err.code == "R4_so_number_in_job":
        _mark_error(
            row,
            f"SO# is not allowed in JOB cell: {row.raw_job!r}",
            suggestion=(
                "SO# (Sales Order Number) belongs in MFG NOTES, not the JOB cell. "
                "Move the SO# value to MFG NOTES and use a part-number-style "
                "reference in the JOB cell (e.g. 'RONC 332-0034 revB')."
            ),
        )


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
    sheet_kind: SheetKind,
) -> bool:
    """Write all field values from a staging row onto a Job.

    Pre:  row and job are attached to session.  sheet_kind matches the row's batch.
    Post: All non-identity fields updated.  resolved_ship_date recomputed.
          Returns False (and marks row errored) if _apply_shipped fails.
          When job.status == shipped AND sheet_kind == live AND raw_shipped
          is blank, routes to _apply_unship instead of _apply_shipped.
    Raises: never.
    """
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

    incoming_blank = not row.raw_shipped or not row.raw_shipped.strip()

    if (
        job.status is JobStatus.shipped
        and sheet_kind is SheetKind.live
        and incoming_blank
    ):
        _apply_unship(row, job)
    else:
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

    Parse is attempted before any mutation so that an unparseable value never
    leaves ``job.status`` set to shipped with ``job.shipped_at`` still None.
    On the first successful ship transition, ``job.ever_shipped_at`` is set
    to the parsed date and never overwritten on subsequent ship events
    (INV-S3).
    """
    raw = row.raw_shipped
    if not raw or not raw.strip():
        return True
    parsed = extract_shipped_date(raw)
    if parsed is None:
        _mark_error(
            row,
            f"Unparseable SHIPPED date: {raw!r}",
            suggestion="SHIPPED must be blank or a date like '2025-09-15' or '9/15/2025'.",
        )
        return False
    job.status = JobStatus.shipped
    job.shipped_at = parsed
    if job.ever_shipped_at is None:
        job.ever_shipped_at = parsed
    return True


def _apply_unship(row: ImportStagingRow, job: Job) -> None:
    """Transition a shipped job back to planned when SCHD blanks its SHIPPED cell.

    Pre:   row.raw_shipped is None or whitespace-only.
           job.status == JobStatus.shipped.
           job.ever_shipped_at IS NOT NULL (INV-S1).
           Caller has verified the batch is SheetKind.live.
    Post:  job.status = JobStatus.planned.
           job.shipped_at = None.
           job.ever_shipped_at unchanged (INV-S3).
           No row error is marked.
    Raises: AssertionError when INV-S1 is violated (shipped job without
            ever_shipped_at).  The Stage 5 SAVEPOINT handler converts this
            to a row-level error and names the job_id in the message so the
            operator can reconcile the corrupted row manually.
    """
    assert job.ever_shipped_at is not None, (
        f"INV-S1 violated: job id={job.id} has status=shipped but "
        f"ever_shipped_at IS NULL.  Migration backfill may have missed this row."
    )
    previous_shipped_at = job.shipped_at
    job.status = JobStatus.planned
    job.shipped_at = None
    log.info(
        "transform.unship",
        extra={
            "job_id": job.id,
            "batch_id": row.batch_id,
            "previous_shipped_at": previous_shipped_at,
            "ever_shipped_at": job.ever_shipped_at,
        },
    )


def effective_decomposition(
    row: ImportStagingRow,
) -> JobDecomposition | DecomposeError | None:
    """Return the decomposition Stage 5 should use for this staging row.

    Phase 19: parser no longer accepts a registry, so this wrapper does not
    forward one.  Operator overrides (review_part_number_override,
    review_split_suffix_override) take precedence over the raw_job parse exactly
    as before — the override path is unchanged.

    Override invariant (pre-existing, Phase 18a; explicitly NOT modified by Phase 19):
        A non-null review_split_suffix_override is honored ONLY when
        review_part_number_override is also non-null.  When the part-number
        override is null, the suffix override is silently ignored and the raw_job
        decomposition is returned in full.

    Tight coupling note: this function reads the review-override columns added
    in Phase 18a migration 0009.  Its existence is the single point where the
    transform layer couples to the review-workflow state.

    Pre:  staging_row.raw_job is the original cell text.
          review_part_number_override is null OR both override columns are non-null
          (caller-upheld invariant; see Override invariant above).
    Post: Returns a JobDecomposition reflecting review overrides when both
          override columns are non-null, or the raw_job decomposition otherwise.
          Returns DecomposeError as a VALUE when raw_job parsing fails.
          Returns None when raw_job is empty/None.
    Raises: never.
    """
    parsed = decompose_job_string_with_diagnostic(row.raw_job) if row.raw_job else None
    if parsed is None or isinstance(parsed, DecomposeError):
        return parsed
    if row.review_part_number_override is None:
        return parsed
    return parsed.with_overrides(
        part_number=row.review_part_number_override,
        split_suffix=row.review_split_suffix_override,
    )


# ---------------------------------------------------------------------------
# 2nd OPS carry-forward (Phase 22 2.3)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class JobIdentity:
    """The five-field tuple that identifies a Job independently of its row id.

    Pre:  the fields are the decomposition outputs the active-job lookup used.
    Post: immutable; equality is structural.
    """

    assembly_id: int
    build_type: BuildType | None
    split_suffix: str | None
    repeat_reference: str | None
    build_qualifier: BuildQualifier | None


class CarryForwardKind(str, enum.Enum):
    """Outcome vocabulary for carry_forward_second_ops.

      copied                     - a donor was found and its record transferred.
      skipped_already_populated  - new_job already carried a record. The arm
                                   that makes a second call safe.
      skipped_no_donor           - no qualifying donor. The common case.
    """

    copied = "copied"
    skipped_already_populated = "skipped_already_populated"
    skipped_no_donor = "skipped_no_donor"


@dataclass(frozen=True)
class CarryForwardOutcome:
    donor_job_id: int | None
    line_count: int
    kind: CarryForwardKind


def carry_forward_second_ops(
    session: Session,
    new_job: Job,
    identity: JobIdentity,
) -> CarryForwardOutcome:
    """Copy the 2nd OPS record of the most recently discarded job sharing
    new_job identity onto new_job.

    The failure this closes: sweep_missing_planned_jobs discards a planned job
    absent from a day SCHD, the identity lookup excludes discarded rows, and
    the same job reappearing tomorrow inserts a NEW Job with a new id - leaving
    the audit data stranded on a row the grid can never show.

    Pre:  new_job is flushed and carries an id.
          identity is the five-field tuple that just failed the active lookup.
          new_job audit state is NOT a pre-condition - it is tested below. The
          call site sits inside Stage 5 per-row SAVEPOINT loop, where an
          unenforced pre-condition is a liability and a checked branch is not.
    Post: returns exactly one of:
            skipped_already_populated - new_job.second_ops_reviewed_at was
                already set. Nothing read, nothing written.
            skipped_no_donor          - no qualifying donor. Nothing written.
            copied                    - a donor was found: identical identity,
                discarded_at IS NOT NULL, second_ops_reviewed_at IS NOT NULL,
                chosen by (discarded_at DESC, id DESC). Its lines are copied
                preserving line_order, and both
                second_ops_unexpected_inclusions and second_ops_reviewed_at are
                copied VERBATIM - reviewed_at is not restamped, the audit
                happened when it happened. The donor is not mutated.
          Idempotent by construction: the already_populated arm makes the second
          and every later call a no-op, so no caller has to guarantee single
          invocation.
          Cause of discard is not considered: a job listed as scheduled again is
          legitimately live, and its prior physical-inspection record being
          attached is useful.
    Donor visibility: sweep_missing_planned_jobs is Stage 6b and runs AFTER the
          Stage 5 transform loop, so a donor discarded_at is always written by
          a prior, committed batch. There is no intra-batch flush ordering to
          arrange. Recorded because the reverse ordering is the intuitive guess
          and the failure it would cause is silent.
    Raises: never.
    """
    if new_job.second_ops_reviewed_at is not None:
        log.warning(
            "transform.second_ops.carry_forward.already_populated",
            extra={"new_job_id": new_job.id, "job_identity": repr(identity)},
        )
        return CarryForwardOutcome(
            donor_job_id=None,
            line_count=0,
            kind=CarryForwardKind.skipped_already_populated,
        )

    donor = session.execute(
        select(Job)
        .where(Job.assembly_id == identity.assembly_id)
        .where(Job.build_type == identity.build_type)
        .where(Job.split_suffix.is_not_distinct_from(identity.split_suffix))
        .where(Job.repeat_reference.is_not_distinct_from(identity.repeat_reference))
        .where(Job.build_qualifier.is_not_distinct_from(identity.build_qualifier))
        .where(Job.discarded_at.is_not(None))
        .where(Job.second_ops_reviewed_at.is_not(None))
        .where(Job.id != new_job.id)
        .order_by(Job.discarded_at.desc(), Job.id.desc())
        .limit(1)
    ).scalar_one_or_none()

    if donor is None:
        return CarryForwardOutcome(
            donor_job_id=None, line_count=0, kind=CarryForwardKind.skipped_no_donor
        )

    donor_lines = session.scalars(
        select(JobSecondOpsLine)
        .where(JobSecondOpsLine.job_id == donor.id)
        .order_by(JobSecondOpsLine.line_order)
    ).all()

    for donor_line in donor_lines:
        session.add(
            JobSecondOpsLine(
                job_id=new_job.id,
                line_order=donor_line.line_order,
                find_number=donor_line.find_number,
                component_part_number=donor_line.component_part_number,
                per_board_count=donor_line.per_board_count,
                ref_des=donor_line.ref_des,
                description=donor_line.description,
                mount_type=donor_line.mount_type,
                quantity_needed=donor_line.quantity_needed,
                quantity_on_hand=donor_line.quantity_on_hand,
            )
        )

    new_job.second_ops_unexpected_inclusions = donor.second_ops_unexpected_inclusions
    new_job.second_ops_reviewed_at = donor.second_ops_reviewed_at
    session.flush()

    log.info(
        "transform.second_ops.carry_forward.copied",
        extra={
            "donor_job_id": donor.id,
            "new_job_id": new_job.id,
            "line_count": len(donor_lines),
            "job_identity": repr(identity),
        },
    )
    return CarryForwardOutcome(
        donor_job_id=donor.id,
        line_count=len(donor_lines),
        kind=CarryForwardKind.copied,
    )


def transform_staging_row(
    session: Session,
    row: ImportStagingRow,
    *,
    sheet_kind: SheetKind = SheetKind.live,
) -> TransformOutcome:
    """Transform a single pending staging row into a Job upsert.

    Pre:  row.batch_id is set.  sheet_kind matches the row's batch sheet_kind.
    Post: Either inserts or updates a Job and marks the row processed, or marks
          the row errored.  Returns TransformOutcome describing the action taken.
    Raises: never (all exceptions result in a row-level error).
    """
    decomp_result = effective_decomposition(row)
    if decomp_result is None or isinstance(decomp_result, DecomposeError):
        _mark_decompose_error(row, decomp_result)
        return TransformOutcome(None, "errored")
    decomp: JobDecomposition = decomp_result

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
        .where(Job.build_qualifier.is_not_distinct_from(decomp.build_qualifier))
        .where(Job.superseded_at.is_(None))
        .where(Job.discarded_at.is_(None))
    ).scalar_one_or_none()

    if existing is None:
        job = Job(
            assembly_id=assembly.id,
            customer_id=customer.id,
            build_type=decomp.build_type,
            split_suffix=decomp.split_suffix,
            repeat_reference=decomp.repeat_reference,
            build_qualifier=decomp.build_qualifier,
            quantity=quantity,
        )
        session.add(job)
        action: Literal["inserted", "updated"] = "inserted"
    else:
        incoming_blank = not row.raw_shipped or not row.raw_shipped.strip()

        if existing.status is JobStatus.shipped:
            if sheet_kind is SheetKind.live:
                # Both the un-ship (blank) and re-ship (new date) paths fall
                # through to the standard update branch; _apply_row_to_job
                # routes between _apply_unship and _apply_shipped via §3.6.
                pass
            else:
                # Historical (AA) ingest of a shipped job: preserve the
                # existing conflict error verbatim.
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

    if not _apply_row_to_job(session, row, job, decomp=decomp, sheet_kind=sheet_kind):
        return TransformOutcome(None, "errored")

    if action == "inserted":
        session.flush()
        carry_forward_second_ops(
            session,
            job,
            JobIdentity(
                assembly_id=assembly.id,
                build_type=decomp.build_type,
                split_suffix=decomp.split_suffix,
                repeat_reference=decomp.repeat_reference,
                build_qualifier=decomp.build_qualifier,
            ),
        )

    _apply_lines(row, job)

    row.build_qualifier = decomp.build_qualifier
    row.processing_status = ImportStatus.processed
    session.flush()
    row.resolved_job_id = job.id
    row.processed_at = _now()

    return TransformOutcome(job, action)
