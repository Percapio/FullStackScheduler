from pathlib import Path

import pytest

from backend.app.ingest import DuplicateBatchError, ingest_workbook


def test_duplicate_hash_raises_without_force(workbook_factory, session_factory):
    rows = [{"JOB": "137845\nNEW", "QTY": "10", "CUSTOMER": "ACME"}]
    path = workbook_factory(rows)

    ingest_workbook(path, session_factory=session_factory)

    with pytest.raises(DuplicateBatchError):
        ingest_workbook(path, session_factory=session_factory)


def test_force_flag_overrides_sha_collision(workbook_factory, session_factory):
    rows = [{"JOB": "137845\nNEW", "QTY": "10", "CUSTOMER": "ACME"}]
    path = workbook_factory(rows)

    first = ingest_workbook(path, session_factory=session_factory)

    result = ingest_workbook(path, force=True, session_factory=session_factory)
    assert result.duplicate_of_batch_id == first.batch_id
    assert result.rows_total == 1


def test_different_files_produce_different_hashes(workbook_factory, session_factory):
    path_a = workbook_factory(
        [{"JOB": "137845\nNEW", "QTY": "10", "CUSTOMER": "ACME"}],
        filename="a.xlsx",
    )
    path_b = workbook_factory(
        [{"JOB": "137846\nRONC", "QTY": "5", "CUSTOMER": "Beta"}],
        filename="b.xlsx",
    )

    r1 = ingest_workbook(path_a, session_factory=session_factory)
    r2 = ingest_workbook(path_b, session_factory=session_factory)
    assert r1.source_sha256 != r2.source_sha256
    assert r1.batch_id != r2.batch_id
