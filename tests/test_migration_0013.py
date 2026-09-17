"""Smoke tests for migration 0013 — split-suffix provenance (Patch 06 §4.2).

Verifies:
- 0013 upgrades cleanly on top of 0012.
- The backfill classifies rows: no override -> NULL; suffix equal to the one
  PUT /canonical would compute -> 'computed'; any other suffix -> 'operator'.
- The CHECK constraint rejects values outside {'computed', 'operator'}.
- Downgrade drops the column and leaves the table's indexes intact.
"""
from __future__ import annotations

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError


def _alembic_cfg(db_url: str) -> Config:
    cfg = Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", db_url)
    cfg.config_file_name = None
    import logging
    logging.getLogger("alembic").setLevel(logging.WARNING)
    return cfg


# (row id, raw_job, original_raw_job, part-number override, suffix override, expected source)
_FIXTURE_ROWS = [
    (1, "137845-1par\nNEW", None, None, None, None),
    (2, "137845-1par\nNEW", "137845-1par\nNEW", "137845", "-1par", "computed"),
    (3, "137845-1par\nNEW", "137845-1par\nNEW", "137845", "-9par", "operator"),
    (4, "137845\nNEW", None, "654321", None, "computed"),
    (5, "137845-1par\nNEW", None, "137845", None, "operator"),
]


@pytest.fixture()
def db_at_0012(tmp_path):
    db_path = tmp_path / "migrate_0013_test.db"
    db_url = f"sqlite:///{db_path}"
    engine = create_engine(db_url, connect_args={"check_same_thread": False})
    cfg = _alembic_cfg(db_url)
    command.upgrade(cfg, "0012")
    with engine.begin() as conn:
        conn.execute(text(
            "INSERT INTO import_batches (id, source_file, status, sheet_kind, row_count, "
            "created_at, updated_at) VALUES (1, 't.xlsx', 'awaiting_review', 'live', 5, "
            "CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
        ))
        for row_id, raw_job, original, pn_override, suffix_override, _ in _FIXTURE_ROWS:
            conn.execute(
                text(
                    "INSERT INTO import_staging (id, batch_id, source_row_number, raw_job, "
                    "original_raw_job, review_part_number_override, review_split_suffix_override, "
                    "processing_status, created_at, updated_at) VALUES (:id, 1, :id, :raw_job, "
                    ":original, :pn, :suffix, 'pending', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
                ),
                {"id": row_id, "raw_job": raw_job, "original": original,
                 "pn": pn_override, "suffix": suffix_override},
            )
    yield engine, cfg
    engine.dispose()


def _index_names(conn) -> set[str]:
    return {r[1] for r in conn.execute(text("PRAGMA index_list(import_staging)")).fetchall()}


class TestMigration0013:
    def test_upgrade_backfills_source_by_rule(self, db_at_0012):
        engine, cfg = db_at_0012
        command.upgrade(cfg, "0013")
        with engine.connect() as conn:
            sources = dict(conn.execute(text(
                "SELECT id, review_split_suffix_source FROM import_staging"
            )).fetchall())
        assert sources == {row[0]: row[5] for row in _FIXTURE_ROWS}

    def test_check_constraint_rejects_unknown_source(self, db_at_0012):
        engine, cfg = db_at_0012
        command.upgrade(cfg, "0013")
        with engine.connect() as conn:
            with pytest.raises(IntegrityError):
                conn.execute(text(
                    "UPDATE import_staging SET review_split_suffix_source = 'typed' WHERE id = 1"
                ))

    def test_upgrade_preserves_indexes(self, db_at_0012):
        engine, cfg = db_at_0012
        with engine.connect() as conn:
            before = _index_names(conn)
        command.upgrade(cfg, "0013")
        with engine.connect() as conn:
            assert _index_names(conn) == before

    def test_downgrade_drops_column(self, db_at_0012):
        engine, cfg = db_at_0012
        with engine.connect() as conn:
            before = _index_names(conn)
        command.upgrade(cfg, "0013")
        command.downgrade(cfg, "0012")
        with engine.connect() as conn:
            columns = {c[1] for c in conn.execute(text("PRAGMA table_info(import_staging)")).fetchall()}
            table_sql = conn.execute(text(
                "SELECT sql FROM sqlite_master WHERE name = 'import_staging'"
            )).scalar()
            row_count = conn.execute(text("SELECT COUNT(*) FROM import_staging")).scalar()
            assert _index_names(conn) == before
        assert "review_split_suffix_source" not in columns
        assert "ck_import_staging_split_suffix_source" not in table_sql
        assert row_count == len(_FIXTURE_ROWS)
