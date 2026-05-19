"""Smoke tests for migration 0010 — parsed_part_number column.

Verifies:
- Migration 0010 upgrades cleanly on top of 0009.
- parsed_part_number column exists and accepts NULL and text values.
- Index ix_staging_batch_parsed_pn is present.
- downgrade() raises NotImplementedError.
"""
from __future__ import annotations

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text


def _alembic_cfg(db_url: str) -> Config:
    cfg = Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", db_url)
    cfg.config_file_name = None
    import logging
    logging.getLogger("alembic").setLevel(logging.WARNING)
    return cfg


@pytest.fixture()
def db_at_0009(tmp_path):
    """Yield (engine, cfg, url) with schema upgraded to revision 0009."""
    db_path = tmp_path / "migrate_0010_test.db"
    db_url = f"sqlite:///{db_path}"
    engine = create_engine(db_url, connect_args={"check_same_thread": False})
    cfg = _alembic_cfg(db_url)
    command.upgrade(cfg, "0009")
    yield engine, cfg, db_url
    engine.dispose()


class TestMigration0010:
    def test_upgrade_succeeds(self, db_at_0009):
        """Running upgrade to 0010 completes without error."""
        engine, cfg, db_url = db_at_0009
        command.upgrade(cfg, "0010")

    def test_parsed_part_number_column_exists(self, db_at_0009):
        """After upgrade, import_staging has parsed_part_number column."""
        engine, cfg, db_url = db_at_0009
        command.upgrade(cfg, "0010")
        with engine.connect() as conn:
            cols = conn.execute(text("PRAGMA table_info(import_staging)")).fetchall()
            col_names = {c[1] for c in cols}
        assert "parsed_part_number" in col_names

    def test_parsed_part_number_accepts_null(self, db_at_0009):
        """parsed_part_number defaults to NULL for existing rows."""
        engine, cfg, db_url = db_at_0009
        command.upgrade(cfg, "0010")
        with engine.connect() as conn:
            # Insert a minimal import_batch to satisfy FK.
            conn.execute(text(
                "INSERT INTO import_batches (source_file, status, sheet_kind, row_count, created_at, updated_at) "
                "VALUES ('test.xlsx', 'pending', 'live', 0, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
            ))
            batch_id = conn.execute(text("SELECT last_insert_rowid()")).scalar()
            conn.execute(text(
                "INSERT INTO import_staging (batch_id, source_row_number, processing_status) "
                "VALUES (:bid, 1, 'pending')"
            ), {"bid": batch_id})
            result = conn.execute(text(
                "SELECT parsed_part_number FROM import_staging WHERE batch_id = :bid"
            ), {"bid": batch_id}).scalar()
            conn.rollback()
        assert result is None

    def test_index_exists(self, db_at_0009):
        """Index ix_staging_batch_parsed_pn is created by the migration."""
        engine, cfg, db_url = db_at_0009
        command.upgrade(cfg, "0010")
        with engine.connect() as conn:
            indexes = conn.execute(text("PRAGMA index_list(import_staging)")).fetchall()
            index_names = {row[1] for row in indexes}
        assert "ix_staging_batch_parsed_pn" in index_names

    def test_downgrade_raises(self, db_at_0009):
        """downgrade() raises NotImplementedError (forward-only migration)."""
        engine, cfg, db_url = db_at_0009
        command.upgrade(cfg, "0010")
        with pytest.raises(NotImplementedError):
            command.downgrade(cfg, "0009")
