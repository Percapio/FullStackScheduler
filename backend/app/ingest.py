from __future__ import annotations

import argparse
import hashlib
import logging
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Literal

from openpyxl import load_workbook
from sqlalchemy import select
from sqlalchemy.orm import Session

from . import reader
from .db import SessionLocal
from .config import get_settings
from .extractors import decompose_job_string, decompose_job_string_with_diagnostic, DecomposeError
from .models import Assembly, BuildQualifier, BuildType, ImportBatch, ImportStagingRow, ImportStatus, SheetKind
from .services.jobs_lifecycle import CandidateDelta, detect_supersession_candidates
from .services.staging import _rollback_with_error_capture
from .transform import transform_staging_row, _mark_decompose_error

log = logging.getLogger(__name__)


class DuplicateBatchError(Exception):
    def __init__(self, existing_batch_id: int, source_sha256: str):
        self.existing_batch_id = existing_batch_id
        self.source_sha256 = source_sha256
        super().__init__(
            f"duplicate of batch={existing_batch_id} sha={source_sha256}"
        )


class ReaderError(Exception):
    pass


# ---------------------------------------------------------------------------
# Review workflow types (Phase 18a §6.2 / Phase 18c §6.2)
# ---------------------------------------------------------------------------

@dataclass
class IdentityTuple:
    """Full job identity tuple used by Stage 3.6 intra-file duplicate detection.

    Mirrors the five-column uniqueness key on the Job table.
    """
    part_number: str
    build_type: BuildType
    split_suffix: str | None
    repeat_reference: str | None
    build_qualifier: BuildQualifier | None

    def as_key(self) -> tuple:
        """Return a hashable tuple for dict-keying."""
        return (
            self.part_number,
            self.build_type,
            self.split_suffix,
            self.repeat_reference,
            self.build_qualifier,
        )


@dataclass
class ReviewGroup:
    """One parsed_part_number and the staging rows that share it.

    Pre:  parsed_part_number is the part_number produced by the decomposition
          of row.raw_job for every row in rows.
    Post: rows is non-empty.
    identity: populated only for intra_file_duplicates groups (Phase 18c §6.2);
              None for new_b_numbers and new_non_b_numbers groups.
    """
    parsed_part_number: str
    rows: list[ImportStagingRow]
    identity: IdentityTuple | None = None


@dataclass
class ReviewClassification:
    """Output of classify_new_parts_for_review (Phase 18b form).

    b:                     new pure-digit canonicals (matches_b_number_shape returns
                           True; predicate is ``^\\d{5,6}$`` per Phase 19).  Exactly
                           5 or 6 digits; values outside that range are routed to
                           non_b.
    non_b:                 new canonicals that are not pure-digit.  Populated under
                           Phase 18b; empty only when no non-B# new parts are present.
    intra_file_duplicates: groups of staging rows sharing identity within the same
                           workbook.  Currently always empty; populated by Phase 2 §7.5.
    """
    b: list[ReviewGroup] = field(default_factory=list)
    non_b: list[ReviewGroup] = field(default_factory=list)
    intra_file_duplicates: list[ReviewGroup] = field(default_factory=list)

    def is_empty(self) -> bool:
        """True iff every list field is empty (no rows require review)."""
        return not self.b and not self.non_b and not self.intra_file_duplicates


_B_NUMBER_RE = re.compile(r"^\d{5,6}$")


def matches_b_number_shape(pn: str) -> bool:
    """Return True iff pn is exactly 5 or 6 decimal digits.

    Phase 19 alignment: the length contract matches DIGIT_SHAPE_PATTERN in
    extractors.py.  A canonical that does not fast-fire the shape rule cannot
    classify as a B#.

    Pre:  pn is the canonical produced by decompose_job_string_with_diagnostic.
    Post: deterministic; pure function.
    Raises: never.
    """
    return _B_NUMBER_RE.fullmatch(pn) is not None


def load_assembly_part_numbers(session: Session) -> set[str]:
    """Load every Assembly.part_number into a set for O(1) membership tests.

    Pre:  session is the ingest's outer transaction.
    Post: returns every Assembly.part_number string currently in the DB.
    Raises: propagates DB errors.
    """
    return set(session.scalars(select(Assembly.part_number)).all())


def classify_new_parts_for_review(
    session: Session,
    staging: list[ImportStagingRow],
    registry: set[str],
) -> ReviewClassification:
    """Identify staging rows whose parsed part_number is absent from the registry.

    Phase 18b form: every new-part row (B# and non-B# alike) is pre-approved
    as 'verified'.  The operator still reviews the panel but no action is
    required before POST /confirm is callable.

    Pre:  staging contains every row from this batch with raw_job populated.
          session is the same outer transaction as Stage 3.
          registry is the Stage 2.5 snapshot from load_assembly_part_numbers.
    Post: sets row.review_status = 'verified' and row.parsed_part_number on
          every new-part row with a successful decomposition.
          rows where decomposition fails keep review_status = NULL (excluded
          from the review panel; resolve as Stage 5 errors post-confirm).
    Raises: never.
    """
    new_b_groups: dict[str, list[ImportStagingRow]] = {}
    new_non_b_groups: dict[str, list[ImportStagingRow]] = {}

    for row in staging:
        if not row.raw_job:
            continue
        decomp = decompose_job_string_with_diagnostic(row.raw_job)
        if decomp is None or isinstance(decomp, DecomposeError):
            continue
        # P-5: persist parsed_part_number for all rows with a successful decomp
        # so the API read path can use the indexed column instead of re-parsing.
        row.parsed_part_number = decomp.part_number
        if decomp.part_number in registry:
            continue
        target_group = new_b_groups if matches_b_number_shape(decomp.part_number) else new_non_b_groups
        target_group.setdefault(decomp.part_number, []).append(row)
        row.review_status = "verified"

    return ReviewClassification(
        b=[ReviewGroup(pn, rows) for pn, rows in new_b_groups.items()],
        non_b=[ReviewGroup(pn, rows) for pn, rows in new_non_b_groups.items()],
        intra_file_duplicates=[],
    )


def augment_with_intra_file_duplicates(
    classification: ReviewClassification,
    staging: list[ImportStagingRow],
    registry: set[str],
) -> ReviewClassification:
    """Detect rows in the same batch that share a full identity tuple and group them.

    Stage 3.6 — runs after classify_new_parts_for_review (Stage 3.5).

    Pre:  classification is the output of classify_new_parts_for_review.
          staging contains every row from this batch.
          registry is the Stage 2.5 snapshot.
    Post: returns a new ReviewClassification whose intra_file_duplicates list
          contains one ReviewGroup per colliding identity tuple.  Rows that are
          members of a duplicate group and have no existing review_status are
          set to 'verified' (pre-approval default, F7).
          classification.b and classification.non_b are carried through unchanged.
    Raises: never.
    """
    by_identity: dict[tuple, list[ImportStagingRow]] = {}
    for row in staging:
        if not row.raw_job:
            continue
        decomp = decompose_job_string_with_diagnostic(row.raw_job)
        if decomp is None or isinstance(decomp, DecomposeError):
            continue
        identity = IdentityTuple(
            part_number=decomp.part_number,
            build_type=decomp.build_type,
            split_suffix=decomp.split_suffix,
            repeat_reference=decomp.repeat_reference,
            build_qualifier=decomp.build_qualifier,
        )
        by_identity.setdefault(identity.as_key(), []).append((identity, row))

    duplicates: list[ReviewGroup] = []
    for key, pairs in by_identity.items():
        if len(pairs) < 2:
            continue
        identity_obj, _ = pairs[0]
        rows = [r for _, r in pairs]
        for row in rows:
            if row.review_status is None:
                row.review_status = "verified"
        duplicates.append(ReviewGroup(
            parsed_part_number=identity_obj.part_number,
            rows=rows,
            identity=identity_obj,
        ))

    return ReviewClassification(
        b=classification.b,
        non_b=classification.non_b,
        intra_file_duplicates=duplicates,
    )


@dataclass(frozen=True)
class IngestResult:
    """Sum-type representing the outcome of ingest_workbook.

    kind == "processed_or_error": batch ran through Stage 4..6 immediately.
    kind == "held_for_review":    batch is paused in awaiting_review status.
    """
    kind: Literal["processed_or_error", "held_for_review"]
    batch_id: int
    source_sha256: str
    filename: str | None

    # Populated only when kind == "processed_or_error"
    rows_total: int | None = None
    rows_inserted: int | None = None
    rows_updated: int | None = None
    rows_errored: int | None = None
    duplicate_of_batch_id: int | None = None
    sheet_kind: SheetKind | None = None
    candidates_opened: int | None = None
    candidates_auto_returned: int | None = None

    # Populated only when kind == "held_for_review"
    new_b_numbers: list[ReviewGroup] | None = None
    new_non_b_numbers: list[ReviewGroup] | None = None
    intra_file_duplicates: list[ReviewGroup] | None = None

    @classmethod
    def processed_or_error(
        cls,
        *,
        batch_id: int,
        source_sha256: str,
        filename: str | None,
        rows_total: int,
        rows_inserted: int,
        rows_updated: int,
        rows_errored: int,
        duplicate_of_batch_id: int | None,
        sheet_kind: SheetKind,
        candidates_opened: int,
        candidates_auto_returned: int,
    ) -> "IngestResult":
        return cls(
            kind="processed_or_error",
            batch_id=batch_id,
            source_sha256=source_sha256,
            filename=filename,
            rows_total=rows_total,
            rows_inserted=rows_inserted,
            rows_updated=rows_updated,
            rows_errored=rows_errored,
            duplicate_of_batch_id=duplicate_of_batch_id,
            sheet_kind=sheet_kind,
            candidates_opened=candidates_opened,
            candidates_auto_returned=candidates_auto_returned,
        )

    @classmethod
    def held_for_review(
        cls,
        *,
        batch_id: int,
        source_sha256: str,
        filename: str | None,
        new_b_numbers: list[ReviewGroup],
        new_non_b_numbers: list[ReviewGroup],
        intra_file_duplicates: list[ReviewGroup],
    ) -> "IngestResult":
        return cls(
            kind="held_for_review",
            batch_id=batch_id,
            source_sha256=source_sha256,
            filename=filename,
            new_b_numbers=new_b_numbers,
            new_non_b_numbers=new_non_b_numbers,
            intra_file_duplicates=intra_file_duplicates,
        )


_COLUMN_MAP: dict[str, str] = {
    "SHIPPED": "raw_shipped",
    "PCB NOTES": "raw_pcb_notes",
    "KIT NOTES": "raw_kit_notes",
    "SCHEDULING NOTES": "raw_scheduling_notes",
    "LINE 1": "raw_line_1",
    "LINE 2": "raw_line_2",
    "LINE 3": "raw_line_3",
    "JOB": "raw_job",
    "QTY": "raw_qty",
    "SHIP DATE": "raw_ship_date",
    "PROG": "raw_prog",
    "MFG NOTES": "raw_mfg_notes",
    "SMT LINES": "raw_smt_lines",
    "SMT PLCMNTS": "raw_smt_plcmnts",
    "SHIP METHOD": "raw_ship_method",
    "CUSTOMER": "raw_customer",
    "SALES P": "raw_sales_p",
    "DOC REL": "raw_doc_rel",
    "KIT REL": "raw_kit_rel",
    "CODE": "raw_code",
    "BOM COMPARE / PHOTOS": "raw_bom_compare_photos",
}


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def ingest_workbook(
    path: str | Path,
    *,
    sheet: str = reader.SHEET_NAME,
    force: bool = False,
    session_factory: Callable[[], Session] = SessionLocal,
) -> IngestResult:
    path = Path(path)
    sha = _sha256(path)

    # Stage 1 — duplicate guard.
    # Abandoned batches are excluded so an operator can re-upload after abandoning
    # a held batch (§6.6).  Processed/error batches are still guarded; force=True
    # overrides them as before.
    duplicate_of: int | None = None
    session = session_factory()
    try:
        existing_batch_id = session.execute(
            select(ImportBatch.id)
            .where(ImportBatch.source_sha256 == sha)
            .where(ImportBatch.status != ImportStatus.abandoned)
            .limit(1)
        ).scalar_one_or_none()

        if existing_batch_id is not None:
            if not force:
                session.close()
                raise DuplicateBatchError(existing_batch_id, sha)
            duplicate_of = existing_batch_id
    finally:
        session.close()

    # Stage 2 — read workbook
    try:
        _wb = load_workbook(str(path), data_only=True)
        resolved_sheet = reader.resolve_sheet(_wb, sheet)
        sheet_kind = reader.classify_sheet(resolved_sheet)
        raw_rows = list(reader.read_rows(str(path), resolved_sheet))
    except Exception as exc:
        raise ReaderError(str(exc)) from exc

    rows_total = len(raw_rows)

    # Stage 3 — create batch & stage rows
    session = session_factory()
    try:
        batch = ImportBatch(
            source_file=path.name,
            source_sha256=sha,
            row_count=rows_total,
            status=ImportStatus.pending,
            sheet_kind=sheet_kind,
        )
        session.add(batch)
        session.flush()

        staging_rows: list[ImportStagingRow] = []
        for row_number, cells in raw_rows:
            kwargs: dict[str, object] = {
                "batch_id": batch.id,
                "source_row_number": row_number,
            }
            for header, attr in _COLUMN_MAP.items():
                kwargs[attr] = cells.get(header)
            staging_row = ImportStagingRow(**kwargs)
            session.add(staging_row)
            staging_rows.append(staging_row)

        session.flush()  # assigns IDs to staging rows before Stage 3.5 runs
        batch_id = batch.id

        log.info(
            "ingest.sheet_kind_resolved",
            extra={
                "batch_id": batch_id,
                "requested": sheet,
                "resolved": resolved_sheet,
                "kind": sheet_kind.value,
            },
        )

        # Stage 2.5 — pre-load registry for decomposition and classification.
        # The snapshot is passed through Stage 3.5 and Stage 3.6.  Stage 5
        # reloads a fresh snapshot at confirm time (see run_stages_4_to_6).
        registry = load_assembly_part_numbers(session)

        # Stage 3.5 — classify new parts for review.
        # Phase 18b: B# and non-B# new parts alike are held; rows are
        # pre-approved (review_status='verified') so the operator can confirm
        # immediately without any per-row action.
        classification = classify_new_parts_for_review(session, staging_rows, registry)

        # Stage 3.6 — detect intra-file duplicates on full identity tuple.
        # Groups colliding rows into intra_file_duplicates for operator review
        # instead of letting Stage 4 mark them as hard errors.
        classification = augment_with_intra_file_duplicates(classification, staging_rows, registry)

        if not classification.is_empty():
            # Hold the batch; Stage 4..6 runs at POST /confirm.
            batch.status = ImportStatus.awaiting_review
            session.commit()
            return IngestResult.held_for_review(
                batch_id=batch_id,
                source_sha256=sha,
                filename=path.name,
                new_b_numbers=classification.b,
                new_non_b_numbers=classification.non_b,
                intra_file_duplicates=classification.intra_file_duplicates,
            )

        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()

    # Stages 4–6 — collision scan, per-row transform, batch finalization.
    return run_stages_4_to_6(
        batch_id=batch_id,
        rows_total=rows_total,
        sheet_kind=sheet_kind,
        source_sha256=sha,
        filename=path.name,
        duplicate_of=duplicate_of,
        session_factory=session_factory,
    )


def run_stages_4_to_6(
    batch_id: int,
    rows_total: int,
    sheet_kind: SheetKind,
    source_sha256: str,
    filename: str | None,
    duplicate_of: int | None,
    session_factory: Callable[[], Session],
) -> IngestResult:
    """Run Stage 4 (collision scan), Stage 5 (transform), Stage 5b (supersession),
    and Stage 6 (finalize) for a batch that is already staged.

    Used by both ingest_workbook (immediate path) and POST /confirm (deferred path).

    Pre:  The batch and all its ImportStagingRows already exist in the DB.
          Rows with discarded_at IS NOT NULL are excluded from processing.
    Post: batch.status is set to processed or error; returns IngestResult.
    Raises: propagates exceptions from Stage 5b or Stage 6; callers should
            not catch silently.
    """
    counters = {"inserted": 0, "updated": 0, "errored": 0}
    session = session_factory()
    try:
        # Rows with discarded_at set were hard-deleted during review (D3).
        pending = session.scalars(
            select(ImportStagingRow)
            .where(ImportStagingRow.batch_id == batch_id)
            .where(ImportStagingRow.processing_status == ImportStatus.pending)
            .where(ImportStagingRow.discarded_at.is_(None))
            .order_by(ImportStagingRow.source_row_number)
        ).all()

        # Stage 5 reload: fresh registry snapshot at confirm time so that
        # assemblies created by sibling batches between Stage 2.5 and confirm
        # are visible to the registry-driven parser (§5.4 split-snapshot contract).
        stage5_registry = load_assembly_part_numbers(session)

        loop_exc: Exception | None = None
        try:
            # Stage 4 — intra-file collision scan.
            # When intra_file_collision_legacy_error_path is False (default),
            # Stage 3.6 has already surfaced duplicates for review; Stage 4
            # short-circuits the collision block.  Setting it True restores
            # the pre-Phase-18c behaviour as a rollback affordance (§6.3).
            settings = get_settings()
            by_identity: dict[tuple, list[ImportStagingRow]] = {}
            for row in pending:
                decomp_result = decompose_job_string_with_diagnostic(row.raw_job) if row.raw_job else None
                if decomp_result is None or isinstance(decomp_result, DecomposeError):
                    _mark_decompose_error(row, decomp_result)
                    continue
                decomp = decomp_result
                key = (decomp.part_number, decomp.build_type, decomp.split_suffix, decomp.repeat_reference, decomp.build_qualifier)
                by_identity.setdefault(key, []).append(row)

            if settings.intra_file_collision_legacy_error_path:
                for identity, rows in by_identity.items():
                    if len(rows) < 2:
                        continue
                    other_ids = sorted(r.id for r in rows)
                    qualifier_segment: str = identity[4].value if identity[4] else ""
                    canonical_key: str = f"{identity[0]}|{identity[1].value}|{identity[2] or ''}|{identity[3] or ''}|{qualifier_segment}"
                    for row in rows:
                        row.duplicate_group_key = canonical_key
                        row.processing_status = ImportStatus.error
                        row.processing_error = (
                            f"Intra-file duplicate JOB identity "
                            f"{canonical_key} "
                            f"(staging rows {other_ids})"
                        )
                        row.suggested_correction = (
                            "This JOB identity appears more than once in the workbook. "
                            "Add a split suffix to distinguish the builds \u2014 e.g., change the JOB to "
                            f"'{identity[0]}-1par', '{identity[0]}-2par', etc."
                        )

            counters["errored"] += sum(
                1 for r in pending if r.processing_status is ImportStatus.error
            )

            # Stage 5 — per-row transform (SAVEPOINT on shared session)
            for row in pending:
                if row.processing_status is not ImportStatus.pending:
                    continue
                nested = session.begin_nested()
                try:
                    outcome = transform_staging_row(
                        session, row, sheet_kind=sheet_kind
                    )
                    if outcome.action == "errored":
                        _rollback_with_error_capture(session, row, nested)
                        counters["errored"] += 1
                    else:
                        nested.commit()
                        counters[outcome.action] += 1
                except Exception as exc:
                    nested.rollback()
                    row.processing_status = ImportStatus.error
                    row.processing_error = (
                        f"Transform failure: {type(exc).__name__}: {exc}"
                    )
                    row.processed_at = datetime.now(timezone.utc).replace(tzinfo=None)
                    counters["errored"] += 1

            # Stage 5b — detect supersession candidates (live batches only).
            # Runs inside the same outer transaction as Stage 5; failures
            # propagate to the loop_exc handler and force batch.status = error.
            delta = CandidateDelta.empty()
            if sheet_kind == SheetKind.live:
                live_batch = session.get(ImportBatch, batch_id)
                delta = detect_supersession_candidates(session, live_batch)

        except Exception as exc:
            session.rollback()
            loop_exc = exc

        # Stage 6 — finalize batch (runs in both success and failure paths,
        # before session.commit(), so the batch never lingers as `pending`).
        batch_obj = session.get(ImportBatch, batch_id)
        batch_obj.row_count = rows_total
        batch_obj.status = (
            ImportStatus.error
            if loop_exc is not None or counters["errored"] > 0
            else ImportStatus.processed
        )

        session.commit()

        if loop_exc is not None:
            raise loop_exc
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()

    return IngestResult.processed_or_error(
        batch_id=batch_id,
        source_sha256=source_sha256,
        filename=filename,
        rows_total=rows_total,
        rows_inserted=counters["inserted"],
        rows_updated=counters["updated"],
        rows_errored=counters["errored"],
        duplicate_of_batch_id=duplicate_of,
        sheet_kind=sheet_kind,
        candidates_opened=len(delta.new_pending_candidate_ids),
        candidates_auto_returned=len(delta.auto_returned_candidate_ids),
    )



def main() -> None:
    parser = argparse.ArgumentParser(
        prog="python -m backend.app.ingest",
        description="Ingest an Excel workbook into the schedule database.",
    )
    parser.add_argument("path", help="Path to the .xlsx workbook")
    parser.add_argument(
        "--sheet", default=reader.SHEET_NAME,
        help=f'Worksheet to ingest (default: "{reader.SHEET_NAME}")',
    )
    parser.add_argument(
        "--force", action="store_true",
        help="Proceed even if an ImportBatch with the same SHA256 exists.",
    )
    args = parser.parse_args()

    try:
        result = ingest_workbook(args.path, sheet=args.sheet, force=args.force)
    except DuplicateBatchError as exc:
        print(
            f"ingest: duplicate of batch={exc.existing_batch_id}. "
            f"Pass --force to re-ingest.",
            file=sys.stderr,
        )
        sys.exit(2)
    except ReaderError as exc:
        print(f"ingest: reader error: {exc}", file=sys.stderr)
        sys.exit(2)

    if result.kind == "held_for_review":
        _cli_exit_held(result)
    else:
        _cli_exit_processed(result)


def _cli_exit_processed(result: "IngestResult") -> None:  # noqa: F821
    dup = f" (duplicate_of={result.duplicate_of_batch_id})" if result.duplicate_of_batch_id else ""
    print(
        f"ingest: batch={result.batch_id} sha={result.source_sha256[:8]}…"
        f" total={result.rows_total}"
        f" inserted={result.rows_inserted}"
        f" updated={result.rows_updated}"
        f" errored={result.rows_errored}{dup}"
    )
    sys.exit(1 if result.rows_errored > 0 else 0)


def _cli_exit_held(result: "IngestResult") -> None:  # noqa: F821
    # Exit code 3 signals held-for-review to callers / CI pipelines.
    print(
        f"ingest: batch={result.batch_id} sha={result.source_sha256[:8]}…"
        f" held_for_review"
        f" new_b={len(result.new_b_numbers or [])}"
        f" new_non_b={len(result.new_non_b_numbers or [])}"
    )
    for group in result.new_b_numbers or []:
        print(f"  new B#: {group.parsed_part_number}  ({len(group.rows)} row(s))")
    sys.exit(3)


if __name__ == "__main__":
    main()
