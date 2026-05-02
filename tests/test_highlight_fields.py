from __future__ import annotations

import ast
import pathlib

import pytest

from backend.app.errors import HIGHLIGHT_RULES, resolve_highlight_fields
from backend.app.models import ImportStagingRow, ImportStatus


# --- Pure resolver tests -------------------------------------------------------


def test_resolve_each_rule_substring():
    for needle, fields in HIGHLIGHT_RULES:
        assert resolve_highlight_fields(needle) == fields


def test_resolve_unmatched_returns_empty():
    assert resolve_highlight_fields("nothing matches this string") == []


def test_resolve_none_returns_empty():
    assert resolve_highlight_fields(None) == []


# --- Static scan over _mark_error call sites (F5) -----------------------------


_FILES_TO_SCAN = ["backend/app/transform.py", "backend/app/ingest.py"]


def _iter_mark_error_messages():
    """Yield (file, lineno, message_str) for every static _mark_error(row, <msg>, …) call."""
    for f in _FILES_TO_SCAN:
        src = pathlib.Path(f).read_text()
        tree = ast.parse(src, filename=f)
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "_mark_error"
                and len(node.args) >= 2
            ):
                msg_arg = node.args[1]
                if isinstance(msg_arg, ast.Constant) and isinstance(msg_arg.value, str):
                    yield f, node.lineno, msg_arg.value
                elif isinstance(msg_arg, ast.JoinedStr) and msg_arg.values:
                    first = msg_arg.values[0]
                    if isinstance(first, ast.Constant) and isinstance(first.value, str):
                        yield f, node.lineno, first.value
                    else:
                        pytest.fail(
                            f"{f}:{node.lineno} — _mark_error message has no constant prefix; "
                            f"add a HIGHLIGHT_RULES rule and a string prefix, or extend this test."
                        )
                else:
                    pytest.fail(
                        f"{f}:{node.lineno} — _mark_error message is fully dynamic; "
                        f"refactor to a string-prefixed f-string or extend this test."
                    )


def test_resolve_only_one_rule_per_message():
    """For every _mark_error literal, EXACTLY one HIGHLIGHT_RULES substring must match."""
    for f, lineno, msg in _iter_mark_error_messages():
        hits = [needle for needle, _ in HIGHLIGHT_RULES if needle in msg]
        assert len(hits) == 1, (
            f"{f}:{lineno} — message {msg!r} matches {len(hits)} rules: {hits}. "
            f"Either add a rule for this message (zero hits) or tighten an existing "
            f"substring (multiple hits)."
        )


# --- API integration -----------------------------------------------------------


def test_get_staging_row_includes_highlight_fields(client, session, open_batch):
    row = ImportStagingRow(
        batch_id=open_batch.id,
        source_row_number=1,
        processing_status=ImportStatus.error,
        processing_error="Invalid QTY: '0'",
        raw_qty="0",
    )
    session.add(row)
    session.commit()

    resp = client.get(f"/api/staging/{row.id}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["highlight_fields"] == ["raw_qty"]


def test_get_staging_row_unmatched_message_returns_empty_list(client, session, open_batch):
    row = ImportStagingRow(
        batch_id=open_batch.id,
        source_row_number=2,
        processing_status=ImportStatus.error,
        processing_error="freeform text not in any rule",
    )
    session.add(row)
    session.commit()

    resp = client.get(f"/api/staging/{row.id}")
    assert resp.status_code == 200
    body = resp.json()
    assert "highlight_fields" in body
    assert body["highlight_fields"] == []
