"""Smoke tests for migration 0012 — 2nd OPS.

Verifies:
- 0012 upgrades cleanly on top of 0011 and downgrades back to it.
- job_second_ops_lines exists with the declared widths and its composite index.
- jobs gains both nullable columns with no server default, so pre-existing rows
  read back as `unaudited`.
- ON DELETE CASCADE reaches the child rows.
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
def db_at_0011(tmp_path):
    db_path = tmp_path / "migrate_0012_test.db"
    db_url = f"sqlite:///{db_path}"
    engine = create_engine(db_url, connect_args={"check_same_thread": False})
    cfg = _alembic_cfg(db_url)
    command.upgrade(cfg, "0011")
    yield engine, cfg
    engine.dispose()


class TestMigration0012:
    def test_upgrade_succeeds(self, db_at_0011):
        engine, cfg = db_at_0011
        command.upgrade(cfg, "0012")

    def test_lines_table_columns_and_widths(self, db_at_0011):
        engine, cfg = db_at_0011
        command.upgrade(cfg, "0012")
        with engine.connect() as conn:
            cols = conn.execute(
                text("PRAGMA table_info(job_second_ops_lines)")
            ).fetchall()
        declared = {c[1]: c[2] for c in cols}
        assert declared["find_number"] == "VARCHAR(32)"
        assert declared["component_part_number"] == "VARCHAR(128)"
        assert declared["per_board_count"] == "VARCHAR(32)"
        assert declared["ref_des"] == "VARCHAR(2048)"
        assert declared["description"] == "VARCHAR(255)"
        assert declared["mount_type"] == "VARCHAR(16)"
        assert declared["quantity_needed"] == "VARCHAR(32)"
        assert declared["quantity_on_hand"] == "VARCHAR(32)"

    def test_no_unbounded_line_columns(self, db_at_0011):
        """ref_des is the one that will be tempting to widen back to TEXT."""
        engine, cfg = db_at_0011
        command.upgrade(cfg, "0012")
        with engine.connect() as conn:
            cols = conn.execute(
                text("PRAGMA table_info(job_second_ops_lines)")
            ).fetchall()
        text_columns = [c[1] for c in cols if c[2] == "TEXT"]
        assert text_columns == []

    def test_composite_index_present(self, db_at_0011):
        engine, cfg = db_at_0011
        command.upgrade(cfg, "0012")
        with engine.connect() as conn:
            names = {
                r[1]
                for r in conn.execute(
                    text("PRAGMA index_list(job_second_ops_lines)")
                ).fetchall()
            }
        assert "ix_second_ops_job_order" in names

    def test_jobs_columns_added_nullable_without_default(self, db_at_0011):
        engine, cfg = db_at_0011
        command.upgrade(cfg, "0012")
        with engine.connect() as conn:
            cols = {
                c[1]: c for c in conn.execute(text("PRAGMA table_info(jobs)")).fetchall()
            }
        for name in ("second_ops_reviewed_at", "second_ops_unexpected_inclusions"):
            assert name in cols
            assert cols[name][3] == 0        # notnull flag clear
            assert cols[name][4] is None     # no server default

    def test_only_note_column_is_unbounded(self, db_at_0011):
        engine, cfg = db_at_0011
        command.upgrade(cfg, "0012")
        with engine.connect() as conn:
            cols = conn.execute(text("PRAGMA table_info(jobs)")).fetchall()
        second_ops_text = [
            c[1] for c in cols if c[1].startswith("second_ops") and c[2] == "TEXT"
        ]
        assert second_ops_text == ["second_ops_unexpected_inclusions"]

    def test_cascade_delete_reaches_lines(self, db_at_0011):
        engine, cfg = db_at_0011
        command.upgrade(cfg, "0012")
        with engine.connect() as conn:
            conn.execute(text("PRAGMA foreign_keys=ON"))
            conn.execute(text("INSERT INTO assemblies (id, part_number) VALUES (1, 'B1')"))
            conn.execute(text("INSERT INTO customers (id, name) VALUES (1, 'Acme')"))
            conn.execute(text(
                "INSERT INTO jobs (id, assembly_id, customer_id, status, quantity, "
                "line_1, line_2, line_3) VALUES (1, 1, 1, 'planned', 5, 0, 0, 0)"
            ))
            conn.execute(text(
                "INSERT INTO job_second_ops_lines (job_id, line_order, find_number) "
                "VALUES (1, 0, '1')"
            ))
            conn.execute(text("DELETE FROM jobs WHERE id = 1"))
            remaining = conn.execute(
                text("SELECT COUNT(*) FROM job_second_ops_lines")
            ).scalar()
        assert remaining == 0

    def test_downgrade_round_trips(self, db_at_0011):
        engine, cfg = db_at_0011
        command.upgrade(cfg, "0012")
        command.downgrade(cfg, "0011")
        with engine.connect() as conn:
            tables = {
                r[0]
                for r in conn.execute(
                    text("SELECT name FROM sqlite_master WHERE type='table'")
                ).fetchall()
            }
            job_columns = {
                c[1] for c in conn.execute(text("PRAGMA table_info(jobs)")).fetchall()
            }
        assert "job_second_ops_lines" not in tables
        assert "second_ops_reviewed_at" not in job_columns
        assert "second_ops_unexpected_inclusions" not in job_columns
