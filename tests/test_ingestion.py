from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy.orm import Session

from backend.app.models import (
    BuildType,
    ImportBatch,
    ImportStagingRow,
    ImportStatus,
)
from backend.app.transform import transform_staging_row


def _add_row(session: Session, batch: ImportBatch, **overrides) -> ImportStagingRow:
    defaults = dict(
        batch_id=batch.id,
        source_row_number=2,
        raw_job="137845\nNEW\n(ITAR)",
        raw_qty="10",
        raw_customer="ACME Aerospace",
        raw_ship_date="4/17\n15D",
        raw_mfg_notes="**warning**: *inspect* before run",
        raw_pcb_notes="clear 4/14",
        raw_kit_notes=None,
        raw_line_2="1",
    )
    defaults.update(overrides)
    row = ImportStagingRow(**defaults)
    session.add(row)
    session.commit()
    return row


def test_transform_creates_job_with_decomposed_assembly(
    session: Session, open_batch: ImportBatch,
):
    row = _add_row(session, open_batch)

    outcome = transform_staging_row(session, row)
    session.commit()

    assert outcome.action == "inserted"
    job = outcome.job
    assert job is not None
    assert job.assembly.part_number == "137845"
    assert job.build_type is BuildType.new
    codes = {c.code for c in job.assembly.classifications}
    assert codes == {"ITAR"}


def test_transform_handles_job_without_classifications(
    session: Session, open_batch: ImportBatch,
):
    row = _add_row(session, open_batch, raw_job="137846\nRONC")

    outcome = transform_staging_row(session, row)
    session.commit()

    assert outcome.action == "inserted"
    job = outcome.job
    assert job is not None
    assert job.assembly.part_number == "137846"
    assert job.build_type is BuildType.ronc
    assert list(job.assembly.classifications) == []


def test_transform_extracts_lead_time_and_ship_date_text(
    session: Session, open_batch: ImportBatch,
):
    row = _add_row(session, open_batch, raw_ship_date="4/17\n15D")

    outcome = transform_staging_row(session, row)
    session.commit()

    job = outcome.job
    assert job is not None
    assert job.ship_date_text == "4/17\n"
    assert job.ship_lead_time_raw == "15D"


def test_transform_preserves_markdown_on_mfg_notes(
    session: Session, open_batch: ImportBatch,
):
    row = _add_row(session, open_batch)

    outcome = transform_staging_row(session, row)
    session.commit()

    assert outcome.job.assembly.base_mfg_notes == "**warning**: *inspect* before run"


def test_transform_captures_clear_date_from_pcb_notes(
    session: Session, open_batch: ImportBatch,
):
    row = _add_row(
        session, open_batch,
        raw_pcb_notes="clear 4/14",
        raw_kit_notes=None,
    )

    outcome = transform_staging_row(session, row)
    session.commit()

    assert outcome.job.notes_clear_date_raw == "4/14"


def test_transform_captures_clear_date_from_kit_notes(
    session: Session, open_batch: ImportBatch,
):
    row = _add_row(
        session, open_batch,
        raw_pcb_notes=None,
        raw_kit_notes="4/14 clear to ship",
    )

    outcome = transform_staging_row(session, row)
    session.commit()

    assert outcome.job.notes_clear_date_raw == "4/14"


def test_transform_clear_date_latest_wins_when_both_present(
    session: Session, open_batch: ImportBatch,
):
    row = _add_row(
        session, open_batch,
        raw_pcb_notes="clear 4/14",
        raw_kit_notes="4/16 clear",
    )

    outcome = transform_staging_row(session, row)
    session.commit()

    assert outcome.job.notes_clear_date_raw == "4/16"


def test_transform_assigns_line(
    session: Session, open_batch: ImportBatch,
):
    row = _add_row(session, open_batch, raw_line_2="1")

    outcome = transform_staging_row(session, row)
    session.commit()

    assert outcome.job.line_2 is True


def test_transform_updates_staging_row_bookkeeping(
    session: Session, open_batch: ImportBatch,
):
    row = _add_row(session, open_batch)

    outcome = transform_staging_row(session, row)
    session.commit()

    assert row.processing_status is ImportStatus.processed
    assert row.resolved_job_id == outcome.job.id
    assert row.processed_at is not None


def test_transform_fails_row_when_build_type_missing(
    session: Session, open_batch: ImportBatch,
):
    row = _add_row(session, open_batch, raw_job="137847")

    outcome = transform_staging_row(session, row)
    session.commit()

    assert outcome.action == "errored"
    assert outcome.job is None
    assert row.processing_status is ImportStatus.error
    assert row.processing_error


def test_transform_rejects_repeat_reference_overflow(
    session: Session, open_batch: ImportBatch,
):
    raw_job = "137845\nROWC " + "x" * 40
    row = _add_row(session, open_batch, raw_job=raw_job)

    outcome = transform_staging_row(session, row)
    session.commit()

    assert outcome.action == "errored"
    assert "repeat_reference overflow" in row.processing_error


def test_transform_rejects_split_suffix_overflow(
    session: Session, open_batch: ImportBatch,
):
    intermediates = "\n".join(f"x{i:02d}" for i in range(20))
    raw_job = f"137845\n{intermediates}\nNEW"
    row = _add_row(session, open_batch, raw_job=raw_job)

    outcome = transform_staging_row(session, row)
    session.commit()

    assert outcome.action == "errored"
    assert "split_suffix overflow" in row.processing_error


def test_apply_lines_yields_concrete_booleans(
    session: Session, open_batch: ImportBatch,
):
    row = _add_row(
        session, open_batch,
        raw_line_1="X", raw_line_2=None, raw_line_3="   ",
    )

    outcome = transform_staging_row(session, row)
    session.commit()

    assert outcome.action == "inserted"
    assert outcome.job.line_1 is True
    assert outcome.job.line_2 is False
    assert outcome.job.line_3 is False


@pytest.mark.parametrize("raw,expected", [
    ("12",    12),
    ("  7 ",  7),
    ("",      None),
    (None,    None),
    ("N/A",   None),
])
def test_apply_smt_feeder_count(
    raw, expected, session: Session, open_batch: ImportBatch,
):
    row = _add_row(session, open_batch, raw_smt_lines=raw)

    outcome = transform_staging_row(session, row)
    session.commit()

    assert outcome.job.smt_feeder_count == expected


@pytest.mark.parametrize("raw,expected", [
    ("1234.56",    Decimal("1234.56")),
    ("$1,234.56",  Decimal("1234.56")),
    ("1,234",      Decimal("1234")),
    ("",           None),
    (None,         None),
    ("TBD",        None),
])
def test_apply_run_cost(
    raw, expected, session: Session, open_batch: ImportBatch,
):
    row = _add_row(session, open_batch, raw_code=raw)

    outcome = transform_staging_row(session, row)
    session.commit()

    assert outcome.job.run_cost == expected
