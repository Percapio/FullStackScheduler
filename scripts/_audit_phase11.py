"""Phase 11 Stage 0 — split-suffix regex drift audit.

Replays the canonical SPLIT_SUFFIX_RE (proposed in Phase 11 Stage 1) over
every processed ImportStagingRow and reports decompositions that disagree
with the persisted Job / Assembly.  Read-only: no DB writes.

Run from repo root:
    python scripts/_audit_phase11.py [--out <path>] [--db <url>]

Defaults:
    --out  <script_dir>/audit_phase11.csv
    --db   SCHEDULER_DATABASE_URL env var, else sqlite:///./outputs/db/schedule.db

Exit codes:
    0  audit completed (counts on stdout; CSV written)
    1  fatal (DB unreachable or CSV unwritable)
"""
from __future__ import annotations

import argparse
import csv
import os
import re
import sys
from dataclasses import dataclass, fields as dataclass_fields
from pathlib import Path
from typing import Literal

# Ensure repo root is on sys.path when the script is run directly
# (e.g. `python scripts/_audit_phase11.py` from repo root or any cwd).
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

# ---------------------------------------------------------------------------
# Phase 11 Stage 1 preview — canonical SPLIT_SUFFIX_RE
#
# This is the regex that Stage 1 will install in extractors.py in place of
# the permissive _SUFFIX_RE.  Defining it here lets the audit compare what
# Phase 11 *would* produce against what is already persisted.
# ---------------------------------------------------------------------------
_SPLIT_SUFFIX_LEXICON: tuple[str, ...] = ("par", "bal", "ser")
_NEW_SPLIT_SUFFIX_RE = re.compile(
    r"-(?P<digits>\d+)(?P<token>" + "|".join(_SPLIT_SUFFIX_LEXICON) + r")$",
    re.IGNORECASE,
)


def _new_parse_part_line(line: str) -> tuple[str, str | None]:
    """Apply Phase-11 SPLIT_SUFFIX_RE to a stripped, non-empty line.

    Pre:  line is stripped and non-empty (caller guarantees).
    Post: returns (part_number, split_suffix).  split_suffix includes the
          leading "-" and is lower-cased; None when the new regex has no match.
    Raises: never.
    """
    m = _NEW_SPLIT_SUFFIX_RE.search(line)
    if m is None:
        return line, None
    return line[: m.start()].rstrip(), line[m.start() :].lower()


# ---------------------------------------------------------------------------
# DriftRow — one entry per staging row whose new-regex decomposition
# disagrees with the persisted Job / Assembly.
# ---------------------------------------------------------------------------
@dataclass
class DriftRow:
    staging_row_id: int
    source_row_number: int
    raw_job: str
    persisted_part_number: str
    persisted_split_suffix: str | None
    new_part_number: str | None
    new_split_suffix: str | None
    drift_kind: Literal["suffix_split", "now_errors"]


# ---------------------------------------------------------------------------
# New decomposer (Phase 11 preview)
#
# Mirrors the control flow of decompose_job_string_with_diagnostic but uses
# _new_parse_part_line for line 0.  Unchanged helpers are imported from
# extractors so they stay in sync with any other in-flight changes.
# Only part_number and split_suffix are returned — the other fields
# (repeat_reference, classifications) are not needed for the drift check.
# ---------------------------------------------------------------------------
def _new_decompose_part_and_suffix(raw: str) -> tuple[str | None, str | None]:
    """Return (new_part_number, new_split_suffix) under Phase-11 rules.

    Pre:  raw is a non-empty string.
    Post: (None, None) on any structural failure (R1 no-classifier, R2
          multiple-qualifiers); otherwise the values the new decomposer
          would persist.
    Raises: never.
    """
    from backend.app.extractors import _parse_build_line, _parse_qualifier_line
    from backend.app.models import BuildType

    lines = raw.split("\n")
    if len(lines) < 2:
        tokens = raw.strip().split(None, 1)
        if len(tokens) < 2:
            return None, None
        lines = [tokens[0], tokens[1]]

    line0 = lines[0].strip()
    if not line0:
        return None, None

    part_number, split_suffix = _new_parse_part_line(line0)

    intermediates: list[str] = []
    build_type = None
    build_qualifier = None

    for idx in range(1, len(lines)):
        line = lines[idx]

        if build_type is None:
            candidate_bt = _parse_build_line(line)
            if candidate_bt is not None:
                build_type, _ = candidate_bt
                continue

        candidate_q = _parse_qualifier_line(line)
        if candidate_q is not None:
            if build_qualifier is not None:
                return None, None  # R2 — multiple qualifiers
            build_qualifier, _ = candidate_q
            continue

        stripped = line.strip()
        # Lines starting with '(' are classification tokens, not intermediates.
        if stripped and not stripped.startswith("("):
            intermediates.append(stripped.lower())

    if build_type is None and build_qualifier is None:
        return None, None  # R1 — no classifier found

    if build_type is None:
        # Qualifier-only cell — same default as current decomposer.
        build_type = BuildType.new

    if intermediates:
        parts = [split_suffix] if split_suffix else []
        parts.extend(intermediates)
        split_suffix = " ".join(parts)

    return part_number, split_suffix


# ---------------------------------------------------------------------------
# Core audit
# ---------------------------------------------------------------------------
def audit_suffix_regex_drift(session, out_path: Path) -> dict[str, int]:
    """Query all processed staging rows; write drifted rows to a CSV.

    Pre:  session is open on the production DB; out_path is writable.
    Post: CSV written at out_path (header always present, even when empty).
          Returns {"total_processed_rows": N, "drift_count": N,
                   "new_error_count": N}.
    Raises: IOError on unwritable out_path; DB errors propagate.
    """
    from backend.app.models import Assembly, ImportStagingRow, ImportStatus, Job
    from sqlalchemy import select

    # One JOIN query — returns plain scalar tuples, not ORM objects.
    # This avoids DetachedInstanceError (e3q8): no ORM object lifecycle,
    # no lazy-load refresh, no identity-map expiry concerns.
    stmt = (
        select(
            ImportStagingRow.id,
            ImportStagingRow.source_row_number,
            ImportStagingRow.raw_job,
            Job.split_suffix,
            Assembly.part_number,
        )
        .join(Job, ImportStagingRow.resolved_job_id == Job.id)
        .join(Assembly, Job.assembly_id == Assembly.id)
        .where(ImportStagingRow.processing_status == ImportStatus.processed)
        .where(ImportStagingRow.resolved_job_id.is_not(None))
        .where(ImportStagingRow.raw_job.is_not(None))
    )
    rows = session.execute(stmt).all()

    total = len(rows)
    drift_count = 0
    new_error_count = 0

    csv_field_names = [f.name for f in dataclass_fields(DriftRow)]

    with out_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=csv_field_names)
        writer.writeheader()

        for staging_id, source_row_number, raw_job, persisted_suffix, persisted_part in rows:
            new_part, new_suffix = _new_decompose_part_and_suffix(raw_job)

            if new_part is None:
                drift_kind: Literal["suffix_split", "now_errors"] = "now_errors"
                new_error_count += 1
            elif new_part != persisted_part or new_suffix != persisted_suffix:
                drift_kind = "suffix_split"
            else:
                continue  # no drift — omit from CSV

            _write_drift_row(
                writer,
                DriftRow(
                    staging_row_id=staging_id,
                    source_row_number=source_row_number,
                    raw_job=raw_job,
                    persisted_part_number=persisted_part,
                    persisted_split_suffix=persisted_suffix,
                    new_part_number=new_part,
                    new_split_suffix=new_suffix,
                    drift_kind=drift_kind,
                ),
            )
            drift_count += 1

    return {
        "total_processed_rows": total,
        "drift_count": drift_count,
        "new_error_count": new_error_count,
    }


def _write_drift_row(writer: csv.DictWriter, row: DriftRow) -> None:
    writer.writerow({f.name: getattr(row, f.name) for f in dataclass_fields(row)})


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
def _make_arg_parser() -> argparse.ArgumentParser:
    default_out = Path(__file__).parent / "audit_phase11.csv"
    parser = argparse.ArgumentParser(
        description="Phase 11 Stage 0 — audit split-suffix regex drift against live DB.",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=default_out,
        metavar="PATH",
        help=f"Destination CSV (default: {default_out})",
    )
    parser.add_argument(
        "--db",
        metavar="URL",
        default=None,
        help=(
            "SQLAlchemy DB URL. Falls back to SCHEDULER_DATABASE_URL env var, "
            "then the app default (backend/outputs/db/schedule.db relative to repo root)."
        ),
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _make_arg_parser().parse_args(argv)

    if args.db:
        db_url: str = args.db
    elif os.environ.get("SCHEDULER_DATABASE_URL"):
        db_url = os.environ["SCHEDULER_DATABASE_URL"]
    else:
        # Reuse the same default logic as the app so the path is always correct.
        from backend.app.config import _default_database_url
        db_url = _default_database_url()

    try:
        from sqlalchemy import create_engine
        from sqlalchemy.orm import Session

        engine = create_engine(db_url, connect_args={"check_same_thread": False})
        with Session(engine) as session:
            summary = audit_suffix_regex_drift(session, args.out)
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    print(
        f"audit: total_processed={summary['total_processed_rows']} "
        f"drift={summary['drift_count']} "
        f"new_errors={summary['new_error_count']} "
        f"out={args.out}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
