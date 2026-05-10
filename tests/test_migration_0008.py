"""Tests for migration 0008 (ever_shipped_at column).

§6 of Architecture/20260509-ProjectPhase16.md — schema-level tests.
"""
from __future__ import annotations

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.pool import StaticPool


# ---------------------------------------------------------------------------
# Shared helper: build a fresh in-file SQLite engine at a given revision
# ---------------------------------------------------------------------------

def _alembic_cfg(db_url: str) -> Config:
    cfg = Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", db_url)
    # Clear config_file_name so env.py's fileConfig(...) is skipped.
    # Without this, fileConfig reconfigures logging with disable_existing_loggers=True
    # (Python default), which disables caplog capture for other tests in the session.
    cfg.config_file_name = None
    import logging
    logging.getLogger("alembic").setLevel(logging.WARNING)
    return cfg


@pytest.fixture()
def db_at_0007(tmp_path):
    """Yield (engine, cfg) with schema upgraded to revision 0007."""
    db_path = tmp_path / "migrate_test.db"
    db_url = f"sqlite:///{db_path}"
    engine = create_engine(db_url, connect_args={"check_same_thread": False})
    cfg = _alembic_cfg(db_url)
    command.upgrade(cfg, "0007")
    yield engine, cfg, db_url
    engine.dispose()


@pytest.fixture()
def db_at_0008(tmp_path):
    """Yield (engine, cfg) with schema fully upgraded through 0008."""
    db_path = tmp_path / "migrate_test_0008.db"
    db_url = f"sqlite:///{db_path}"
    engine = create_engine(db_url, connect_args={"check_same_thread": False})
    cfg = _alembic_cfg(db_url)
    command.upgrade(cfg, "0008")
    yield engine, cfg, db_url
    engine.dispose()


def _insert_minimal_job(conn, *, status: str, shipped_at: str | None) -> int:
    """Insert the minimum required rows (customer, assembly, job) and return job id."""
    conn.execute(text(
        "INSERT INTO customers (name, created_at, updated_at) "
        "VALUES ('Migr Test', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
    ))
    cust_id = conn.execute(text("SELECT last_insert_rowid()")).scalar()

    conn.execute(text(
        "INSERT INTO assemblies (part_number, created_at, updated_at) "
        "VALUES ('MIG-001', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
    ))
    asm_id = conn.execute(text("SELECT last_insert_rowid()")).scalar()

    shipped_val = f"'{shipped_at}'" if shipped_at else "NULL"
    conn.execute(text(
        f"INSERT INTO jobs "
        f"(assembly_id, customer_id, build_type, quantity, status, shipped_at, "
        f" line_1, line_2, line_3, created_at, updated_at) "
        f"VALUES ({asm_id}, {cust_id}, 'new', 10, '{status}', {shipped_val}, "
        f"        0, 0, 0, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
    ))
    return conn.execute(text("SELECT last_insert_rowid()")).scalar()


# ---------------------------------------------------------------------------
# test_migration_0008_aborts_on_invs2_violation
# ---------------------------------------------------------------------------

def test_migration_0008_aborts_on_invs2_violation(db_at_0007):
    """Migration 0008 aborts when a job has shipped_at set but status != 'shipped'."""
    engine, cfg, db_url = db_at_0007

    with engine.begin() as conn:
        # Manufacture an INV-S2 violation: shipped_at set on a planned job.
        _insert_minimal_job(conn, status="planned", shipped_at="2026-04-01")

    with pytest.raises(RuntimeError, match="Migration 0008 aborted"):
        command.upgrade(cfg, "0008")

    # Column must not exist after the aborted migration.
    with engine.connect() as conn:
        cols = [
            row[1]
            for row in conn.execute(text("PRAGMA table_info(jobs)")).fetchall()
        ]
    assert "ever_shipped_at" not in cols


# ---------------------------------------------------------------------------
# test_migration_0008_backfills_ever_shipped_at
# ---------------------------------------------------------------------------

def test_migration_0008_backfills_ever_shipped_at(db_at_0007):
    """Migration 0008 backfills ever_shipped_at = shipped_at for shipped rows."""
    engine, cfg, db_url = db_at_0007

    with engine.begin() as conn:
        job_id = _insert_minimal_job(conn, status="shipped", shipped_at="2026-04-01")

    command.upgrade(cfg, "0008")

    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT shipped_at, ever_shipped_at FROM jobs WHERE id = :id"),
            {"id": job_id},
        ).fetchone()

    assert row.ever_shipped_at == "2026-04-01"
    assert row.shipped_at == "2026-04-01"


# ---------------------------------------------------------------------------
# test_migration_0008_backfill_idempotent
# ---------------------------------------------------------------------------

def test_migration_0008_backfill_idempotent(db_at_0008):
    """Re-running the backfill UPDATE does not overwrite a manually-set ever_shipped_at.

    Verifies the WHERE ever_shipped_at IS NULL guard (INV-S3 preservation).
    """
    engine, cfg, db_url = db_at_0008

    with engine.begin() as conn:
        # Insert a shipped job and manually set ever_shipped_at to a different date.
        job_id = _insert_minimal_job(conn, status="shipped", shipped_at="2026-05-01")
        conn.execute(
            text("UPDATE jobs SET ever_shipped_at = '2026-04-01' WHERE id = :id"),
            {"id": job_id},
        )

    # Re-execute the backfill SQL directly.
    with engine.begin() as conn:
        conn.execute(
            text(
                "UPDATE jobs "
                "SET ever_shipped_at = shipped_at "
                "WHERE shipped_at IS NOT NULL "
                "  AND ever_shipped_at IS NULL"
            )
        )
        row = conn.execute(
            text("SELECT ever_shipped_at FROM jobs WHERE id = :id"),
            {"id": job_id},
        ).fetchone()

    # The manually-set value must be preserved (ever_shipped_at IS NULL guard skipped it).
    assert row.ever_shipped_at == "2026-04-01"
