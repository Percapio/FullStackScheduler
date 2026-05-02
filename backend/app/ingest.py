from __future__ import annotations

import argparse
import hashlib
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import reader
from .db import SessionLocal
from .extractors import decompose_job_string
from .models import ImportBatch, ImportStagingRow, ImportStatus
from .transform import transform_staging_row


class DuplicateBatchError(Exception):
    def __init__(self, existing_batch_id: int, source_sha256: str):
        self.existing_batch_id = existing_batch_id
        self.source_sha256 = source_sha256
        super().__init__(
            f"duplicate of batch={existing_batch_id} sha={source_sha256}"
        )


class ReaderError(Exception):
    pass


@dataclass(frozen=True)
class IngestResult:
    batch_id: int
    source_sha256: str
    rows_total: int
    rows_inserted: int
    rows_updated: int
    rows_errored: int
    duplicate_of_batch_id: int | None


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

    # Stage 1 — duplicate guard
    duplicate_of: int | None = None
    session = session_factory()
    try:
        existing_batch_id = session.execute(
            select(ImportBatch.id).where(ImportBatch.source_sha256 == sha).limit(1)
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
        raw_rows = list(reader.read_rows(str(path), sheet))
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
        )
        session.add(batch)
        session.flush()

        for row_number, cells in raw_rows:
            kwargs: dict[str, object] = {
                "batch_id": batch.id,
                "source_row_number": row_number,
            }
            for header, attr in _COLUMN_MAP.items():
                kwargs[attr] = cells.get(header)
            staging_row = ImportStagingRow(**kwargs)
            session.add(staging_row)

        session.flush()
        session.commit()
        batch_id = batch.id
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()

    # Stages 4–6 — collision scan, per-row transform, batch finalization (single session)
    counters = {"inserted": 0, "updated": 0, "errored": 0}
    session = session_factory()
    try:
        pending = session.scalars(
            select(ImportStagingRow)
            .where(ImportStagingRow.batch_id == batch_id)
            .where(ImportStagingRow.processing_status == ImportStatus.pending)
            .order_by(ImportStagingRow.source_row_number)
        ).all()

        loop_exc: Exception | None = None
        try:
            # Stage 4 — intra-file collision scan
            by_identity: dict[tuple, list[ImportStagingRow]] = {}
            for row in pending:
                decomp = decompose_job_string(row.raw_job) if row.raw_job else None
                if decomp is None:
                    row.processing_status = ImportStatus.error
                    row.processing_error = f"Invalid JOB cell: {row.raw_job!r}"
                    row.suggested_correction = (
                        "JOB cell must contain a part number and a build type \u2014 "
                        "e.g., '128764 NEW' or '128764\\nRONC 123456'."
                    )
                    continue
                key = (decomp.part_number, decomp.build_type, decomp.split_suffix, decomp.repeat_reference)
                by_identity.setdefault(key, []).append(row)

            for identity, rows in by_identity.items():
                if len(rows) < 2:
                    continue
                other_ids = sorted(r.id for r in rows)
                for row in rows:
                    row.processing_status = ImportStatus.error
                    row.processing_error = (
                        f"Intra-file duplicate JOB identity "
                        f"{identity[0]}|{identity[1].value}|{identity[2] or ''}|{identity[3] or ''} "
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
                    outcome = transform_staging_row(session, row)
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

        except Exception as exc:
            session.rollback()
            loop_exc = exc

        # Stage 6 — finalize batch (runs in both success and failure paths,
        # before session.commit(), so the batch never lingers as `pending`).
        batch = session.get(ImportBatch, batch_id)
        batch.row_count = rows_total
        batch.status = (
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

    return IngestResult(
        batch_id=batch_id,
        source_sha256=sha,
        rows_total=rows_total,
        rows_inserted=counters["inserted"],
        rows_updated=counters["updated"],
        rows_errored=counters["errored"],
        duplicate_of_batch_id=duplicate_of,
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

    dup = f" (duplicate_of={result.duplicate_of_batch_id})" if result.duplicate_of_batch_id else ""
    print(
        f"ingest: batch={result.batch_id} sha={result.source_sha256[:8]}…"
        f" total={result.rows_total}"
        f" inserted={result.rows_inserted}"
        f" updated={result.rows_updated}"
        f" errored={result.rows_errored}{dup}"
    )

    sys.exit(1 if result.rows_errored > 0 else 0)


if __name__ == "__main__":
    main()
