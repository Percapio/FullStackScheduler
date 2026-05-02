from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi import status as http_status

from .. import reader
from ..ingest import DuplicateBatchError, ReaderError, ingest_workbook

router = APIRouter()

_ALLOWED_SUFFIXES: frozenset[str] = frozenset({".xlsx"})


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

    # NamedTemporaryFile must be closed on Windows before openpyxl can re-open
    # the path; delete=False lets us close-and-reopen, then we explicitly
    # unlink in the outer `finally`.
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

        return {
            "batch_id": result.batch_id,
            "source_sha256": result.source_sha256,
            "rows_total": result.rows_total,
            "rows_inserted": result.rows_inserted,
            "rows_updated": result.rows_updated,
            "rows_errored": result.rows_errored,
            "duplicate_of_batch_id": result.duplicate_of_batch_id,
            "filename": file.filename,
        }
    finally:
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            pass
