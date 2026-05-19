from __future__ import annotations

import heapq
import logging
import shutil
import tempfile
import time
from collections.abc import Callable
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi import status as http_status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import reader
from ..ingest import DuplicateBatchError, ReaderError, ingest_workbook, load_assembly_part_numbers, matches_b_number_shape, run_stages_4_to_6
from ..models import Assembly, ImportBatch, ImportStagingRow, ImportStatus
from .deps import get_session, get_session_factory

router = APIRouter()

log = logging.getLogger(__name__)

_ALLOWED_SUFFIXES: frozenset[str] = frozenset({".xlsx"})

# ---------------------------------------------------------------------------
# Operator identity placeholder (Phase 18a §6.8, §9.2).
# Phase 18b will replace this constant with a real auth-layer value.
# reviewed_by should NOT be read as a real audit trail until then.
# ---------------------------------------------------------------------------
_OPERATOR_IDENTITY: str = "frontend"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _now_utc() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _levenshtein(a: str, b: str) -> int:
    """Simple Levenshtein edit distance between two strings.

    Post: O(len(a)*len(b)) time; O(len(b)) space.
    Raises: never.
    """
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)
    prev = list(range(len(b) + 1))
    for ch_a in a:
        curr = [prev[0] + 1]
        for j, ch_b in enumerate(b):
            curr.append(min(prev[j + 1] + 1, curr[j] + 1, prev[j] + (ch_a != ch_b)))
        prev = curr
    return prev[-1]


def _similar_assemblies_compute(
    session: Session, pn: str, top_n: int = 3
) -> list[dict[str, Any]]:
    """Compute up to top_n nearest assemblies to pn by Levenshtein edit distance.

    Pre:  session is an active DB session.
    Post: returns up to top_n dicts with keys part_number and edit_distance,
          sorted ascending.  Each candidate is computed with a single
          Levenshtein call (not two).
    Performance: O(R log top_n) Levenshtein calls after a length-bucket
                 prefilter that drops candidates whose length differs from pn
                 by more than LENGTH_WINDOW.
    """
    all_pns: list[str] = list(session.scalars(select(Assembly.part_number)).all())
    pn_len = len(pn)
    LENGTH_WINDOW = 3
    candidates = [
        p for p in all_pns
        if abs(len(p) - pn_len) <= LENGTH_WINDOW and p != pn
    ]
    distances = [(_levenshtein(pn, p), p) for p in candidates]
    top = heapq.nsmallest(top_n, distances)
    return [{"part_number": p, "edit_distance": d} for d, p in top]


# ---------------------------------------------------------------------------
# P-2: In-process cache for similar-assemblies results.
#
# The similar-assemblies set for a held batch is invariant until that batch is
# confirmed or abandoned (assemblies are only created during Stage 5 on confirm).
# Keyed by (batch_id, parsed_part_number); evicted on confirm/abandon.
# ---------------------------------------------------------------------------

_similar_cache: dict[tuple[int, str], list[dict[str, Any]]] = {}


def _similar_assemblies_cached(
    session: Session,
    batch_id: int,
    pn: str,
    top_n: int = 3,
) -> list[dict[str, Any]]:
    """Return similar assemblies for pn, using the in-process cache.

    Pre:  session is the request's session; the batch is in awaiting_review.
    Post: on cache hit, returns cached value with no DB round-trip.
          on cache miss, computes via _similar_assemblies_compute, stores, returns.
    Raises: never beyond propagated DB errors.
    """
    key = (batch_id, pn)
    cached = _similar_cache.get(key)
    if cached is not None:
        return cached
    computed = _similar_assemblies_compute(session, pn, top_n)
    _similar_cache[key] = computed
    return computed


def _invalidate_similar_cache(batch_id: int) -> None:
    """Evict all cache entries for batch_id.  Called on confirm and abandon."""
    keys_to_drop = [k for k in _similar_cache if k[0] == batch_id]
    for k in keys_to_drop:
        del _similar_cache[k]


def _compute_split_suffix(original: str, canonical: str) -> str | None:
    """Derive the split_suffix from the first line of original given canonical.

    When canonical is a prefix of the first line, the remainder is the suffix.
    When it is not (e.g., typo correction), returns None; the caller must supply
    a split_suffix explicitly via PATCH /staging-row/{id}/split-suffix.

    Pre:  original is the raw cell text; canonical is the operator-supplied
          canonical part_number.
    Post: returns remainder or None.
    Raises: never.
    """
    first_line = original.split("\n")[0]
    if first_line.startswith(canonical):
        remainder = first_line[len(canonical):]
        return remainder or None
    return None


@contextmanager
def _timed(label: str, **extra: Any):
    """Log elapsed milliseconds for a code block.  Structured for log parsing."""
    started_at = time.perf_counter()
    try:
        yield
    finally:
        elapsed_ms = (time.perf_counter() - started_at) * 1000.0
        log.info("review.timing %s elapsed_ms=%.1f %s",
                 label, elapsed_ms,
                 " ".join(f"{k}={v}" for k, v in extra.items()))


# ---------------------------------------------------------------------------
# P-4 response-shape helpers
# ---------------------------------------------------------------------------

def _row_response(row: ImportStagingRow) -> dict[str, Any]:
    """Serialize a staging row to the MutationResponse.row shape."""
    return {
        "staging_row_id": row.id,
        "review_status": row.review_status,
        "reviewed_at": row.reviewed_at.isoformat() if row.reviewed_at else None,
        "reviewed_by": row.reviewed_by,
        "review_part_number_override": row.review_part_number_override,
        "review_split_suffix_override": row.review_split_suffix_override,
    }


def _group_status_response(pn: str, rows: list[ImportStagingRow]) -> dict[str, Any]:
    """Compute GroupStatusResponse from all rows for a parsed_part_number.

    Pre:  rows is the complete row list for pn (including deleted).
    Post: review_status follows §6.4 derivation; active_row_count excludes deleted.
    """
    non_deleted = [r for r in rows if r.review_status != "deleted"]
    if not non_deleted:
        gs = "verified"
    elif any(r.review_status == "pending" for r in non_deleted):
        gs = "pending"
    elif any(r.review_status == "edited" for r in non_deleted):
        gs = "edited"
    else:
        gs = "verified"
    return {
        "parsed_part_number": pn,
        "review_status": gs,
        "active_row_count": len(non_deleted),
    }


def _row_parsed_pn(row: ImportStagingRow) -> str | None:
    """Return the parsed part_number for a review row.

    Prefers the stored parsed_part_number column (set at Stage 3.5 post-0010);
    falls back to Python-side decomposition for pre-0010 rows.
    """
    if row.parsed_part_number is not None:
        return row.parsed_part_number
    from ..extractors import decompose_job_string_with_diagnostic, DecomposeError
    if not row.raw_job:
        return None
    decomp = decompose_job_string_with_diagnostic(row.raw_job)
    if decomp is None or isinstance(decomp, DecomposeError):
        return None
    return decomp.part_number


def _require_awaiting_review(batch: ImportBatch | None, batch_id: int) -> ImportBatch:
    if batch is None:
        raise HTTPException(status_code=404, detail=f"Batch {batch_id} not found")
    if batch.status != ImportStatus.awaiting_review:
        raise HTTPException(
            status_code=409,
            detail=(
                f"Batch {batch_id} is in status '{batch.status.value}'; "
                f"expected 'awaiting_review'."
            ),
        )
    return batch


def _require_row_in_review_states(
    row: ImportStagingRow | None,
    row_id: int,
    batch_id: int,
    allowed_statuses: set[str],
) -> ImportStagingRow:
    """Return *row* if it exists, belongs to *batch_id*, and its review_status
    is in *allowed_statuses*.

    Raises:
        404 — row is None or belongs to a different batch.
        409 — row.review_status == 'deleted' (idempotency rejection).
        409 — row.review_status not in allowed_statuses.
    """
    if row is None or row.batch_id != batch_id:
        raise HTTPException(status_code=404, detail=f"Staging row {row_id} not found in batch {batch_id}")
    if row.review_status == "deleted":
        raise HTTPException(status_code=409, detail="Row has already been deleted from review")
    if row.review_status not in allowed_statuses:
        raise HTTPException(
            status_code=409,
            detail=(
                f"Row {row_id} has review_status='{row.review_status}'; "
                f"expected one of {sorted(allowed_statuses)}."
            ),
        )
    return row


def _shape_rule_fired_for_row(row: ImportStagingRow) -> bool:
    """Return True iff the B# shape rule fires on the row's first cell line.

    Per E11: the backend is the single source of truth for shape-rule outcome;
    the frontend reads this value from the API response rather than mirroring
    the regex client-side.

    Pre:  row.raw_job or row.original_raw_job is not None.
    Post: one regex match per call; negligible cost.
    Raises: never.
    """
    from ..extractors import shape_rule_would_fire

    source = row.original_raw_job or row.raw_job
    if not source:
        return False
    first_line = source.split("\n")[0].strip()
    return shape_rule_would_fire(first_line)


def _group_view(
    session: Session,
    batch_id: int,
    pn: str,
    rows: list[ImportStagingRow],
    *,
    identity: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the ReviewGroupView dict for one parsed_part_number.

    identity is populated only for intra_file_duplicates groups (Phase 18c §6.2);
    it is None for new_b_numbers and new_non_b_numbers groups.
    """
    non_deleted = [r for r in rows if r.review_status != "deleted"]
    # Derive group-level status per §6.4 normative derivation.
    if not non_deleted:
        group_status = "verified"
    elif any(r.review_status == "pending" for r in non_deleted):
        group_status = "pending"
    elif any(r.review_status == "edited" for r in non_deleted):
        group_status = "edited"
    else:
        group_status = "verified"

    view: dict[str, Any] = {
        "parsed_part_number": pn,
        "rows": [
            {
                "staging_row_id": r.id,
                "source_row_number": r.source_row_number,
                "original_cell_text": r.original_raw_job or r.raw_job,
                "review_part_number_override": r.review_part_number_override,
                "review_split_suffix_override": r.review_split_suffix_override,
                "review_status": r.review_status,
                "shape_rule_fired": _shape_rule_fired_for_row(r),
            }
            for r in rows
        ],
        "similar_assemblies": _similar_assemblies_cached(session, batch_id, pn),
        "review_status": group_status,
    }
    if identity is not None:
        view["identity"] = identity
    return view


def _rows_for_pn(
    session: Session, batch_id: int, pn: str
) -> list[ImportStagingRow]:
    """All staging rows in batch_id whose parsed part_number equals pn.

    Fast path: uses the indexed parsed_part_number column (populated at Stage 3.5
    post-migration 0010).  Back-compat path: falls back to Python-side
    decomposition for pre-0010 rows where the column is NULL.
    """
    rows = list(session.scalars(
        select(ImportStagingRow)
        .where(ImportStagingRow.batch_id == batch_id)
        .where(ImportStagingRow.parsed_part_number == pn)
        .where(ImportStagingRow.review_status.in_(["pending", "verified", "edited", "deleted"]))
    ).all())
    if rows:
        return rows
    # Back-compat: pre-0010 batches have parsed_part_number IS NULL.
    return _rows_for_pn_via_decomposition(session, batch_id, pn)


def _rows_for_pn_via_decomposition(
    session: Session, batch_id: int, pn: str
) -> list[ImportStagingRow]:
    """Fallback: decompose raw_job in Python to find rows matching pn."""
    from ..extractors import decompose_job_string_with_diagnostic, DecomposeError

    candidates = list(session.scalars(
        select(ImportStagingRow)
        .where(ImportStagingRow.batch_id == batch_id)
        .where(ImportStagingRow.review_status.in_(["pending", "verified", "edited", "deleted"]))
    ).all())
    result = []
    for r in candidates:
        if not r.raw_job:
            continue
        decomp = decompose_job_string_with_diagnostic(r.raw_job)
        if decomp is None or isinstance(decomp, DecomposeError):
            continue
        if decomp.part_number == pn:
            result.append(r)
    return result


def _get_non_deleted_group(session: Session, batch_id: int, pn: str) -> list[ImportStagingRow]:
    """Rows for the pn group that are not yet deleted."""
    return [r for r in _rows_for_pn(session, batch_id, pn) if r.review_status != "deleted"]


# ---------------------------------------------------------------------------
# POST /ingest  (existing upload endpoint)
# ---------------------------------------------------------------------------

@router.post("", status_code=http_status.HTTP_200_OK)
def ingest_upload(
    file: UploadFile = File(..., description="Excel workbook (.xlsx) to ingest"),
    force: bool = False,
):
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in _ALLOWED_SUFFIXES:
        raise HTTPException(
            status_code=http_status.HTTP_400_BAD_REQUEST,
            detail=f"Unsupported file type {suffix!r}. Upload an .xlsx workbook.",
        )

    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".xlsx")
    tmp_path = Path(tmp.name)
    try:
        try:
            shutil.copyfileobj(file.file, tmp)
        finally:
            tmp.close()

        try:
            result = ingest_workbook(
                tmp_path,
                sheet=reader.SHEET_NAME,
                force=force,
            )
        except DuplicateBatchError as exc:
            raise HTTPException(
                status_code=http_status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=(
                    f"Duplicate workbook (already imported as batch "
                    f"{exc.existing_batch_id}). Re-submit with the "
                    f"'Re-ingest' option to override."
                ),
            ) from exc
        except ReaderError as exc:
            raise HTTPException(
                status_code=http_status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Reader error: {exc}",
            ) from exc
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(
                status_code=http_status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"{type(exc).__name__}: {exc}",
            ) from exc

        if result.kind == "held_for_review":
            return {
                "batch_id": result.batch_id,
                "source_sha256": result.source_sha256,
                "filename": file.filename,
                "requires_review": True,
                "new_b_numbers": [
                    {"parsed_part_number": g.parsed_part_number, "row_count": len(g.rows)}
                    for g in (result.new_b_numbers or [])
                ],
                "new_non_b_numbers": [
                    {"parsed_part_number": g.parsed_part_number, "row_count": len(g.rows)}
                    for g in (result.new_non_b_numbers or [])
                ],
                "intra_file_duplicates": [
                    {"parsed_part_number": g.parsed_part_number, "row_count": len(g.rows)}
                    for g in (result.intra_file_duplicates or [])
                ],
            }

        return {
            "batch_id": result.batch_id,
            "source_sha256": result.source_sha256,
            "rows_total": result.rows_total,
            "rows_inserted": result.rows_inserted,
            "rows_updated": result.rows_updated,
            "rows_errored": result.rows_errored,
            "duplicate_of_batch_id": result.duplicate_of_batch_id,
            "filename": file.filename,
            "requires_review": False,
        }
    finally:
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            pass


# ---------------------------------------------------------------------------
# GET /ingest/awaiting-review
# NOTE: This literal-path route MUST be declared before /{batch_id}/... routes.
# ---------------------------------------------------------------------------

@router.get("/awaiting-review")
def list_awaiting_review(session: Session = Depends(get_session)):
    """Return up to 50 batches currently in awaiting_review status, newest first."""
    batches = list(session.scalars(
        select(ImportBatch)
        .where(ImportBatch.status == ImportStatus.awaiting_review)
        .order_by(ImportBatch.created_at.desc())
        .limit(50)
    ).all())

    result = []
    for b in batches:
        new_b_count = session.scalar(
            select(func.count(ImportStagingRow.id))
            .where(ImportStagingRow.batch_id == b.id)
            .where(ImportStagingRow.review_status.isnot(None))
        ) or 0
        pending_count = session.scalar(
            select(func.count(ImportStagingRow.id))
            .where(ImportStagingRow.batch_id == b.id)
            .where(ImportStagingRow.review_status == "pending")
        ) or 0
        result.append({
            "batch_id": b.id,
            "source_file": b.source_file,
            "created_at": b.created_at.isoformat() if b.created_at else None,
            "new_b_count": new_b_count,
            "new_non_b_count": 0,
            "pending_row_count": pending_count,
        })
    return result


# ---------------------------------------------------------------------------
# GET /ingest/{batch_id}/review
# ---------------------------------------------------------------------------

@router.get("/{batch_id}/review")
def get_review_payload(batch_id: int, session: Session = Depends(get_session)):
    """Return the full review payload for a held batch."""
    with _timed("get_review_payload", batch_id=batch_id):
        batch = session.get(ImportBatch, batch_id)
        _require_awaiting_review(batch, batch_id)

        # Collect all rows with review_status set (these are the review rows).
        review_rows = list(session.scalars(
            select(ImportStagingRow)
            .where(ImportStagingRow.batch_id == batch_id)
            .where(ImportStagingRow.review_status.isnot(None))
            .order_by(ImportStagingRow.source_row_number)
        ).all())

        from ..extractors import decompose_job_string_with_diagnostic, DecomposeError

        # Load registry for B# vs non-B# new-part classification.
        registry = load_assembly_part_numbers(session)

        # --- new_b_numbers and new_non_b_numbers ---
        # Group rows by parsed_part_number; exclude rows whose pn is already
        # in the registry (those are intra-file duplicates of existing parts,
        # not new parts).
        b_groups_map: dict[str, list[ImportStagingRow]] = {}
        non_b_groups_map: dict[str, list[ImportStagingRow]] = {}
        for row in review_rows:
            pn = row.parsed_part_number
            if pn is None:
                # Back-compat: pre-0010 rows lack the stored column.
                if not row.raw_job:
                    continue
                decomp = decompose_job_string_with_diagnostic(row.raw_job)
                if decomp is None or isinstance(decomp, DecomposeError):
                    continue
                pn = decomp.part_number
            if pn in registry:
                # Existing part — surfaces only via intra_file_duplicates.
                continue
            if matches_b_number_shape(pn):
                b_groups_map.setdefault(pn, []).append(row)
            else:
                non_b_groups_map.setdefault(pn, []).append(row)

        # --- intra_file_duplicates ---
        # Group rows by full identity tuple; any tuple with 2+ rows is a
        # duplicate group.  Rows appear here regardless of registry membership.
        by_identity: dict[tuple, list[tuple]] = {}
        for row in review_rows:
            if not row.raw_job:
                continue
            decomp = decompose_job_string_with_diagnostic(row.raw_job)
            if decomp is None or isinstance(decomp, DecomposeError):
                continue
            key = (
                decomp.part_number,
                decomp.build_type,
                decomp.split_suffix,
                decomp.repeat_reference,
                decomp.build_qualifier,
            )
            by_identity.setdefault(key, []).append((decomp, row))

        intra_file_groups = []
        for key, pairs in by_identity.items():
            if len(pairs) < 2:
                continue
            first_decomp, _ = pairs[0]
            identity_dict = {
                "part_number": first_decomp.part_number,
                "build_type": first_decomp.build_type.value,
                "split_suffix": first_decomp.split_suffix,
                "repeat_reference": first_decomp.repeat_reference,
                "build_qualifier": (
                    first_decomp.build_qualifier.value
                    if first_decomp.build_qualifier is not None
                    else None
                ),
            }
            dup_rows = [r for _, r in pairs]
            intra_file_groups.append(
                _group_view(session, batch_id, first_decomp.part_number, dup_rows, identity=identity_dict)
            )

        b_groups = [_group_view(session, batch_id, pn, rows) for pn, rows in b_groups_map.items()]
        non_b_groups = [_group_view(session, batch_id, pn, rows) for pn, rows in non_b_groups_map.items()]

        return {
            "batch_id": batch_id,
            "new_b_numbers": b_groups,
            "new_non_b_numbers": non_b_groups,
            "intra_file_duplicates": intra_file_groups,
        }


# ---------------------------------------------------------------------------
# PUT /ingest/{batch_id}/canonical/{parsed_part_number}
# ---------------------------------------------------------------------------

@router.put("/{batch_id}/canonical/{parsed_part_number}")
def set_canonical(
    batch_id: int,
    parsed_part_number: str,
    body: dict,
    session: Session = Depends(get_session),
):
    """Apply a canonical part_number override to all non-deleted rows in a group."""
    with _timed("set_canonical", batch_id=batch_id, pn=parsed_part_number):
        batch = session.get(ImportBatch, batch_id)
        _require_awaiting_review(batch, batch_id)

        canonical: str = (body.get("canonical_part_number") or "").strip()
        if not canonical:
            raise HTTPException(status_code=422, detail="canonical_part_number must be non-empty")

        rows = _get_non_deleted_group(session, batch_id, parsed_part_number)
        if not rows:
            raise HTTPException(
                status_code=409,
                detail=f"No active rows for part_number '{parsed_part_number}' (all deleted)"
            )

        for row in rows:
            if row.original_raw_job is None:
                row.original_raw_job = row.raw_job

            suffix = _compute_split_suffix(row.original_raw_job or row.raw_job or "", canonical)

            if suffix is not None and len(suffix) > 32:
                raise HTTPException(
                    status_code=422,
                    detail=f"Computed split_suffix '{suffix}' exceeds 32 characters",
                )

            row.review_part_number_override = canonical
            row.review_split_suffix_override = suffix

            # Determine new review_status: 'verified' if operator reverted to parsed,
            # 'edited' otherwise.
            from ..extractors import decompose_job_string_with_diagnostic, DecomposeError
            parsed_decomp = decompose_job_string_with_diagnostic(row.raw_job) if row.raw_job else None
            parsed_pn = parsed_decomp.part_number if isinstance(parsed_decomp, type(parsed_decomp)) and not isinstance(parsed_decomp, DecomposeError) and parsed_decomp else None
            parsed_suffix = parsed_decomp.split_suffix if parsed_decomp and not isinstance(parsed_decomp, DecomposeError) else None

            if canonical == (parsed_pn or "") and suffix == parsed_suffix:
                row.review_status = "verified"
            else:
                row.review_status = "edited"

            row.reviewed_by = _OPERATOR_IDENTITY
            row.reviewed_at = _now_utc()

        session.commit()
        all_rows = _rows_for_pn(session, batch_id, parsed_part_number)
        return {
            "updated_rows": [_row_response(r) for r in rows],
            "group": _group_status_response(parsed_part_number, all_rows),
        }


# ---------------------------------------------------------------------------
# PATCH /ingest/{batch_id}/staging-row/{row_id}/split-suffix
# ---------------------------------------------------------------------------

@router.patch("/{batch_id}/staging-row/{row_id}/split-suffix")
def patch_split_suffix(
    batch_id: int,
    row_id: int,
    body: dict,
    session: Session = Depends(get_session),
):
    """Override the split_suffix on a specific staging row."""
    batch = session.get(ImportBatch, batch_id)
    _require_awaiting_review(batch, batch_id)

    row = session.get(ImportStagingRow, row_id)
    _require_row_in_review_states(row, row_id, batch_id, {"pending", "verified", "edited"})

    if row.review_part_number_override is None:
        raise HTTPException(
            status_code=409,
            detail="Cannot set split_suffix before canonical part_number has been set. "
                   "Call PUT /canonical first.",
        )

    raw_suffix: str | None = body.get("split_suffix")
    if raw_suffix is not None:
        raw_suffix = raw_suffix.strip() if isinstance(raw_suffix, str) else None
        if not raw_suffix:
            raw_suffix = None

    if raw_suffix is not None and len(raw_suffix) > 32:
        raise HTTPException(status_code=422, detail=f"split_suffix '{raw_suffix}' exceeds 32 characters")

    row.review_split_suffix_override = raw_suffix
    row.review_status = "edited"
    row.reviewed_by = _OPERATOR_IDENTITY
    row.reviewed_at = _now_utc()

    session.commit()
    pn = _row_parsed_pn(row)
    all_rows = _rows_for_pn(session, batch_id, pn) if pn else []
    return {
        "row": _row_response(row),
        "group": _group_status_response(pn or "", all_rows),
    }


# ---------------------------------------------------------------------------
# POST /ingest/{batch_id}/staging-row/{row_id}/verify
# ---------------------------------------------------------------------------

@router.post("/{batch_id}/staging-row/{row_id}/verify")
def verify_staging_row(
    batch_id: int,
    row_id: int,
    session: Session = Depends(get_session),
):
    """Mark a pending review row as verified (operator confirms parsed value is correct)."""
    batch = session.get(ImportBatch, batch_id)
    _require_awaiting_review(batch, batch_id)

    row = session.get(ImportStagingRow, row_id)
    if row is None or row.batch_id != batch_id:
        raise HTTPException(status_code=404, detail=f"Staging row {row_id} not found in batch {batch_id}")

    if row.review_status == "deleted":
        raise HTTPException(status_code=409, detail="Row has been deleted; cannot verify")
    if row.review_status != "pending":
        raise HTTPException(
            status_code=409,
            detail=f"Row review_status is '{row.review_status}'; only 'pending' rows can be verified directly.",
        )

    row.review_status = "verified"
    row.reviewed_by = _OPERATOR_IDENTITY
    row.reviewed_at = _now_utc()

    session.commit()
    pn = _row_parsed_pn(row)
    all_rows = _rows_for_pn(session, batch_id, pn) if pn else []
    return {
        "row": _row_response(row),
        "group": _group_status_response(pn or "", all_rows),
    }


# ---------------------------------------------------------------------------
# DELETE /ingest/{batch_id}/staging-row/{row_id}
# ---------------------------------------------------------------------------

@router.delete("/{batch_id}/staging-row/{row_id}", status_code=200)
def delete_staging_row_from_review(
    batch_id: int,
    row_id: int,
    session: Session = Depends(get_session),
):
    """Hard-discard a staging row during review (D3).  Excluded from Stage 4..6."""
    with _timed("delete_staging_row", batch_id=batch_id, row_id=row_id):
        batch = session.get(ImportBatch, batch_id)
        _require_awaiting_review(batch, batch_id)

        row = session.get(ImportStagingRow, row_id)
        _require_row_in_review_states(row, row_id, batch_id, {"pending", "verified", "edited"})

        row.discarded_at = _now_utc()
        row.review_status = "deleted"

        session.commit()
        pn = _row_parsed_pn(row)
        all_rows = _rows_for_pn(session, batch_id, pn) if pn else []
        return {
            "row": _row_response(row),
            "group": _group_status_response(pn or "", all_rows),
        }


# ---------------------------------------------------------------------------
# POST /ingest/{batch_id}/confirm
# ---------------------------------------------------------------------------

@router.post("/{batch_id}/confirm")
def confirm_review(
    batch_id: int,
    session_factory: Callable[[], Session] = Depends(get_session_factory),
):
    """Run Stage 4..6 against the surviving staging rows and finalize the batch."""
    with _timed("confirm_review", batch_id=batch_id):
        # Open and close the gating session before delegating to run_stages_4_to_6,
        # which opens its own session internally.  This eliminates the double-session
        # overlap that caused pool exhaustion under load (P-7).
        with session_factory() as session:
            batch = session.get(ImportBatch, batch_id)
            _require_awaiting_review(batch, batch_id)

            # Guard: no 'pending' rows may remain.
            pending_count = session.scalar(
                select(func.count(ImportStagingRow.id))
                .where(ImportStagingRow.batch_id == batch_id)
                .where(ImportStagingRow.review_status == "pending")
                .where(ImportStagingRow.discarded_at.is_(None))
            ) or 0
            if pending_count > 0:
                raise HTTPException(
                    status_code=409,
                    detail=f"{pending_count} row(s) still in 'pending' review status. "
                           "Verify or delete each pending row before confirming.",
                )

            rows_total = batch.row_count
            sheet_kind = batch.sheet_kind
            sha = batch.source_sha256 or ""
            fname = batch.source_file
        # session released here — pool slot returned before run_stages_4_to_6 takes one

        result = run_stages_4_to_6(
            batch_id=batch_id,
            rows_total=rows_total,
            sheet_kind=sheet_kind,
            source_sha256=sha,
            filename=fname,
            duplicate_of=None,
            session_factory=session_factory,
        )

    _invalidate_similar_cache(batch_id)
    return {
        "batch_id": result.batch_id,
        "source_sha256": result.source_sha256,
        "rows_total": result.rows_total,
        "rows_inserted": result.rows_inserted,
        "rows_updated": result.rows_updated,
        "rows_errored": result.rows_errored,
        "requires_review": False,
    }


# ---------------------------------------------------------------------------
# POST /ingest/{batch_id}/abandon
# ---------------------------------------------------------------------------

@router.post("/{batch_id}/abandon")
def abandon_review(
    batch_id: int,
    session: Session = Depends(get_session),
):
    """Abandon a held batch.  Staging rows are retained for forensics.

    After abandonment, the same sha256 file can be re-uploaded because Stage 1
    excludes abandoned batches from the duplicate guard (§6.6).
    """
    batch = session.get(ImportBatch, batch_id)
    if batch is None:
        raise HTTPException(status_code=404, detail=f"Batch {batch_id} not found")

    allowed_statuses = {ImportStatus.awaiting_review, ImportStatus.error}
    if batch.status not in allowed_statuses:
        raise HTTPException(
            status_code=409,
            detail=(
                f"Batch {batch_id} is in status '{batch.status.value}'. "
                f"Only batches in 'awaiting_review' or 'error' status can be abandoned."
            ),
        )

    batch.status = ImportStatus.abandoned
    session.commit()
    _invalidate_similar_cache(batch_id)
    return {"batch_id": batch_id, "status": "abandoned"}
