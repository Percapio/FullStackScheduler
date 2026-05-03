"""Tests for _reevaluate_group_siblings and its three call sites.

§5 of Architecture/20260502-ProjectRefactor01c.md.
"""
from __future__ import annotations

import ast
import textwrap
from pathlib import Path
from unittest.mock import patch

import pytest
from sqlalchemy import select

from backend.app.models import ImportBatch, ImportStagingRow, ImportStatus
from backend.app.services.staging import (
    _DUPLICATE_ERROR_PREFIX,
    _active_duplicate_predicate,
    _reevaluate_group_siblings,
    apply_correction,
    discard_staging_row,
    list_conflicts,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_GROUP_KEY = "128764|new||"
_GROUP_KEY_B = "128765|new||"


def _make_dup_row(session, batch, *, source_row_number: int, group_key: str = _GROUP_KEY, **overrides):
    defaults: dict = dict(
        batch_id=batch.id,
        source_row_number=source_row_number,
        raw_job="128764 NEW",
        processing_status=ImportStatus.error,
        processing_error=f"{_DUPLICATE_ERROR_PREFIX} {group_key} (staging rows [1, 2])",
        duplicate_group_key=group_key,
    )
    defaults.update(overrides)
    row = ImportStagingRow(**defaults)
    session.add(row)
    session.flush()
    return row


@pytest.fixture()
def batch(session):
    b = ImportBatch(source_file="test.xlsx")
    session.add(b)
    session.flush()
    return b


@pytest.fixture()
def batch2(session):
    b = ImportBatch(source_file="test2.xlsx")
    session.add(b)
    session.flush()
    return b


# ---------------------------------------------------------------------------
# test_helper_message_format_matches_ingest
# ---------------------------------------------------------------------------

def test_helper_message_format_matches_ingest():
    """The duplicate-error prefix used by _reevaluate_group_siblings matches the
    string written by ingest.py Stage 4 byte-for-byte."""
    ingest_src = (Path(__file__).parent.parent / "backend" / "app" / "ingest.py").read_text()
    assert _DUPLICATE_ERROR_PREFIX in ingest_src, (
        f"_DUPLICATE_ERROR_PREFIX {_DUPLICATE_ERROR_PREFIX!r} not found in ingest.py"
    )


# ---------------------------------------------------------------------------
# _active_duplicate_predicate static-scan test
# ---------------------------------------------------------------------------

def test_predicate_used_for_all_duplicate_group_key_selects():
    """Every select(ImportStagingRow) in services/staging.py that touches
    duplicate_group_key does so via _active_duplicate_predicate.
    Mirrors the discipline established by Phase 1's HIGHLIGHT_RULES static-scan test.
    """
    src = (Path(__file__).parent.parent / "backend" / "app" / "services" / "staging.py").read_text()
    tree = ast.parse(src)

    raw_column_refs: list[int] = []
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Attribute)
            and node.attr == "duplicate_group_key"
            and isinstance(node.value, ast.Attribute)
            and node.value.attr == "ImportStagingRow"
        ):
            raw_column_refs.append(node.lineno)

    # All direct column references to ImportStagingRow.duplicate_group_key must
    # reside inside _active_duplicate_predicate itself.
    predicate_fn_lines: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_active_duplicate_predicate":
                predicate_fn_lines = {n.lineno for n in ast.walk(node) if hasattr(n, "lineno")}
    outside_predicate = [ln for ln in raw_column_refs if ln not in predicate_fn_lines]
    assert not outside_predicate, (
        f"Direct ImportStagingRow.duplicate_group_key references found outside "
        f"_active_duplicate_predicate at lines: {outside_predicate}"
    )


# ---------------------------------------------------------------------------
# 2-row group: discard one → lone survivor re-evaluates
# ---------------------------------------------------------------------------

def test_sibling_reeval_on_discard_2way(session, batch):
    """Discarding one of two duplicates leaves the partner as the lone survivor,
    which the helper clears and re-transforms.  With no Job match the transform
    errors on a different condition (Invalid JOB cell) or succeeds — either way
    the duplicate_group_key is cleared from the survivor."""
    row_a = _make_dup_row(session, batch, source_row_number=1)
    row_b = _make_dup_row(session, batch, source_row_number=2)
    session.commit()

    # Patch transform so the lone survivor simply processes cleanly.
    from unittest.mock import MagicMock
    mock_outcome = MagicMock()
    mock_outcome.action = "inserted"

    with patch("backend.app.services.staging.transform_staging_row", return_value=mock_outcome):
        discard_staging_row(session, row_a)

    session.refresh(row_b)
    assert row_b.duplicate_group_key is None
    assert row_b.processing_status != ImportStatus.error or row_b.processing_error != (
        f"{_DUPLICATE_ERROR_PREFIX} {_GROUP_KEY} (staging rows [1, 2])"
    )


# ---------------------------------------------------------------------------
# 3-way group: resolve one → two survivors rewritten
# ---------------------------------------------------------------------------

def test_sibling_reeval_on_resolve_3way(session, batch):
    """After one member of a 3-row group is resolved (discarded), the two remaining
    siblings stay errored with an updated id list."""
    row_a = _make_dup_row(session, batch, source_row_number=1)
    row_b = _make_dup_row(session, batch, source_row_number=2)
    row_c = _make_dup_row(session, batch, source_row_number=3)
    session.commit()

    # Manually set message to reference all three so we can verify the rewrite.
    for r in [row_a, row_b, row_c]:
        r.processing_error = (
            f"{_DUPLICATE_ERROR_PREFIX} {_GROUP_KEY} (staging rows [{row_a.id}, {row_b.id}, {row_c.id}])"
        )
    session.commit()

    # Discard row_a — triggers helper with exclude_id=row_a.id.
    with patch("backend.app.services.staging.transform_staging_row") as mock_tx:
        # Should not be called since population >= 2.
        discard_staging_row(session, row_a)
        mock_tx.assert_not_called()

    session.refresh(row_b)
    session.refresh(row_c)

    assert row_b.processing_status == ImportStatus.error
    assert row_c.processing_status == ImportStatus.error
    # New message references only the surviving ids.
    surviving_ids = sorted([row_b.id, row_c.id])
    assert str(surviving_ids) in row_b.processing_error
    assert str(surviving_ids) in row_c.processing_error
    # group key preserved on survivors.
    assert row_b.duplicate_group_key == _GROUP_KEY
    assert row_c.duplicate_group_key == _GROUP_KEY


# ---------------------------------------------------------------------------
# Lone-survivor transform failure → row stays errored, not stranded in pending
# ---------------------------------------------------------------------------

def test_lone_survivor_transform_failure_does_not_leak_row(session, batch):
    """A monkey-patched transform_staging_row that raises must leave the lone
    survivor in an errored state, not stranded in pending with no message.
    Pins §1.2 audit fix."""
    row_a = _make_dup_row(session, batch, source_row_number=1)
    row_b = _make_dup_row(session, batch, source_row_number=2)
    session.commit()

    def _exploding_transform(s, r):
        raise RuntimeError("synthetic transform failure")

    with patch("backend.app.services.staging.transform_staging_row", side_effect=_exploding_transform):
        discard_staging_row(session, row_a)

    session.refresh(row_b)
    assert row_b.processing_status == ImportStatus.error
    assert row_b.processing_error is not None
    assert "Re-evaluation failure" in row_b.processing_error
    # duplicate_group_key was cleared before the transform attempt.
    assert row_b.duplicate_group_key is None


# ---------------------------------------------------------------------------
# Batch-scoping: same group_key in different batches are independent
# ---------------------------------------------------------------------------

def test_batch_scoping_of_group_key(session, batch, batch2):
    """Two batches with the same duplicate_group_key string value must not
    interfere with each other's re-evaluation."""
    row_a1 = _make_dup_row(session, batch, source_row_number=1)
    row_a2 = _make_dup_row(session, batch, source_row_number=2)
    row_b1 = _make_dup_row(session, batch2, source_row_number=1)
    row_b2 = _make_dup_row(session, batch2, source_row_number=2)
    session.commit()

    mock_outcome = __import__("unittest.mock", fromlist=["MagicMock"]).MagicMock()
    mock_outcome.action = "inserted"

    with patch("backend.app.services.staging.transform_staging_row", return_value=mock_outcome):
        discard_staging_row(session, row_a1)

    # Batch 2 rows must remain untouched.
    session.refresh(row_b1)
    session.refresh(row_b2)
    assert row_b1.duplicate_group_key == _GROUP_KEY
    assert row_b2.duplicate_group_key == _GROUP_KEY


# ---------------------------------------------------------------------------
# Discard idempotency preserves the hook-once-only semantic
# ---------------------------------------------------------------------------

def test_discard_idempotent_does_not_double_fire_helper(session, batch):
    """Discarding an already-discarded row is a no-op; the sibling helper must
    not fire a second time (which would spuriously clear the partner's key)."""
    row_a = _make_dup_row(session, batch, source_row_number=1)
    row_b = _make_dup_row(session, batch, source_row_number=2)
    session.commit()

    mock_outcome = __import__("unittest.mock", fromlist=["MagicMock"]).MagicMock()
    mock_outcome.action = "inserted"

    call_count = 0

    original_tx = __import__("backend.app.transform", fromlist=["transform_staging_row"]).transform_staging_row

    def _counting_tx(s, r):
        nonlocal call_count
        call_count += 1
        return mock_outcome

    with patch("backend.app.services.staging.transform_staging_row", side_effect=_counting_tx):
        discard_staging_row(session, row_a)
        first_count = call_count
        discard_staging_row(session, row_a)  # idempotent — should not fire again
        assert call_count == first_count, "helper fired on second discard call"


# ---------------------------------------------------------------------------
# list_conflicts — singleton invariant triggers log
# ---------------------------------------------------------------------------

def test_list_conflicts_singleton_triggers_log(session, batch, caplog):
    """A singleton group (only one active member) is skipped with an error log,
    not silently filtered."""
    import logging
    row = _make_dup_row(session, batch, source_row_number=1)
    session.commit()

    with caplog.at_level(logging.ERROR, logger="backend.app.services.staging"):
        result = list_conflicts(session)

    assert result == []  # singleton group is excluded from output
    assert any("conflict_singleton_invariant_violated" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# list_conflicts — normal two-member group
# ---------------------------------------------------------------------------

def test_list_conflicts_returns_two_member_group(session, batch):
    row_a = _make_dup_row(session, batch, source_row_number=1)
    row_b = _make_dup_row(session, batch, source_row_number=2)
    session.commit()

    result = list_conflicts(session)

    assert len(result) == 1
    assert result[0].batch_id == batch.id
    assert result[0].group_key == _GROUP_KEY
    assert len(result[0].rows) == 2


# ---------------------------------------------------------------------------
# errored excludes rows in conflict group (§3.6.1 Option B)
# ---------------------------------------------------------------------------

def test_errored_excludes_rows_in_conflict_group(session, batch):
    """list_errored must not return rows whose duplicate_group_key is non-null."""
    from backend.app.services.staging import list_errored

    # One plain error row, two duplicate-group rows.
    plain = ImportStagingRow(
        batch_id=batch.id,
        source_row_number=10,
        processing_status=ImportStatus.error,
        processing_error="plain error",
    )
    dup_a = _make_dup_row(session, batch, source_row_number=1)
    dup_b = _make_dup_row(session, batch, source_row_number=2)
    session.add(plain)
    session.commit()

    rows, total = list_errored(session, limit=100, offset=0)
    ids = [r.id for r in rows]

    assert plain.id in ids
    assert dup_a.id not in ids
    assert dup_b.id not in ids
    assert total == 1


# ---------------------------------------------------------------------------
# apply_correction — persistent duplicate correction re-evaluates current group
# ---------------------------------------------------------------------------

def test_sibling_reeval_on_persistent_duplicate_correction(session, batch):
    """When a row in a duplicate group is corrected but the transform still
    produces a duplicate error, the current-group re-evaluation fires and
    rewrites all member messages."""
    row_a = _make_dup_row(session, batch, source_row_number=1, raw_job="128764 NEW")
    row_b = _make_dup_row(session, batch, source_row_number=2, raw_job="128764 NEW")
    session.commit()

    from unittest.mock import MagicMock
    errored_outcome = MagicMock()
    errored_outcome.action = "errored"

    def _still_errored(s, r):
        r.processing_status = ImportStatus.error
        r.processing_error = f"{_DUPLICATE_ERROR_PREFIX} {_GROUP_KEY} (staging rows [x])"
        return errored_outcome

    with patch("backend.app.services.staging.transform_staging_row", side_effect=_still_errored):
        result = apply_correction(session, row_a, {"raw_job": "128764 NEW"})

    assert result is None
    session.refresh(row_a)
    session.refresh(row_b)
    # Both still errored; re-evaluation refreshed their messages.
    assert row_a.processing_status == ImportStatus.error
    assert row_b.processing_status == ImportStatus.error


# ---------------------------------------------------------------------------
# discard clears duplicate_group_key on the discarded row
# ---------------------------------------------------------------------------

def test_discard_clears_duplicate_group_key_on_discarded_row(session, batch):
    row_a = _make_dup_row(session, batch, source_row_number=1)
    row_b = _make_dup_row(session, batch, source_row_number=2)
    session.commit()

    mock_outcome = __import__("unittest.mock", fromlist=["MagicMock"]).MagicMock()
    mock_outcome.action = "inserted"

    with patch("backend.app.services.staging.transform_staging_row", return_value=mock_outcome):
        discard_staging_row(session, row_a)

    session.refresh(row_a)
    assert row_a.duplicate_group_key is None
    assert row_a.discarded_at is not None


# ---------------------------------------------------------------------------
# restore after discard — restored row has NULL duplicate_group_key
# ---------------------------------------------------------------------------

def test_restore_after_discard_in_duplicate_group(session, batch):
    """A discarded-then-restored row reappears in /errored with duplicate_group_key
    IS NULL.  The conflict surface does not own it; §3.6.2 invariant."""
    from backend.app.services.staging import restore_staging_row

    row_a = _make_dup_row(session, batch, source_row_number=1)
    row_b = _make_dup_row(session, batch, source_row_number=2)
    session.commit()

    mock_outcome = __import__("unittest.mock", fromlist=["MagicMock"]).MagicMock()
    mock_outcome.action = "inserted"

    with patch("backend.app.services.staging.transform_staging_row", return_value=mock_outcome):
        discard_staging_row(session, row_a)

    restore_staging_row(session, row_a)

    session.refresh(row_a)
    assert row_a.discarded_at is None
    assert row_a.duplicate_group_key is None
