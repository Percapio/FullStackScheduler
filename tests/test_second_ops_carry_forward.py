"""Carry-forward of the 2nd OPS record across a sweep/re-ingest cycle (Phase 22 2.3).

The failure under test: sweep_missing_planned_jobs discards a planned job absent
from a day's SCHD; the identity lookup in transform_staging_row excludes
discarded rows; the same job reappearing tomorrow inserts a NEW Job with a new
id. Without carry-forward the operator's audit data is stranded on a row the
grid can never show.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.models import (
    Assembly,
    BuildType,
    Customer,
    ImportBatch,
    ImportStagingRow,
    Job,
    JobSecondOpsLine,
    JobStatus,
)
from backend.app.transform import (
    CarryForwardKind,
    JobIdentity,
    carry_forward_second_ops,
    transform_staging_row,
)

PART_NUMBER = "137845"


def _identity(assembly_id: int) -> JobIdentity:
    return JobIdentity(
        assembly_id=assembly_id,
        build_type=BuildType.new,
        split_suffix=None,
        repeat_reference=None,
        build_qualifier=None,
    )


def _seed_assembly_and_customer(session: Session) -> tuple[Assembly, Customer]:
    assembly = Assembly(part_number=PART_NUMBER)
    customer = Customer(name="ACME Aerospace")
    session.add_all([assembly, customer])
    session.flush()
    return assembly, customer


def _make_job(
    session: Session,
    assembly: Assembly,
    customer: Customer,
    **overrides,
) -> Job:
    defaults = dict(
        assembly_id=assembly.id,
        customer_id=customer.id,
        quantity=10,
        build_type=BuildType.new,
        status=JobStatus.planned,
    )
    defaults.update(overrides)
    job = Job(**defaults)
    session.add(job)
    session.flush()
    return job


def _audit(
    session: Session,
    job: Job,
    *,
    reviewed_at: datetime,
    note: str | None = "solder bridge on U4",
    line_count: int = 3,
) -> Job:
    for order in range(line_count):
        session.add(
            JobSecondOpsLine(
                job_id=job.id,
                line_order=order,
                find_number=str(order + 1),
                component_part_number=f"CMP-{order}",
                per_board_count="2",
                ref_des=f"C{order}, C{order + 10}",
                description=f"CAP {order}",
                mount_type="SMT",
                quantity_needed="40",
                quantity_on_hand="500",
            )
        )
    job.second_ops_reviewed_at = reviewed_at
    job.second_ops_unexpected_inclusions = note
    session.flush()
    return job


def _lines_for(session: Session, job_id: int) -> list[JobSecondOpsLine]:
    return list(
        session.scalars(
            select(JobSecondOpsLine)
            .where(JobSecondOpsLine.job_id == job_id)
            .order_by(JobSecondOpsLine.line_order)
        ).all()
    )


# ---------------------------------------------------------------------------
# Donor selection
# ---------------------------------------------------------------------------


def test_discarded_audited_donor_is_copied_onto_the_new_job(session: Session):
    assembly, customer = _seed_assembly_and_customer(session)
    reviewed_at = datetime(2026, 8, 1, 9, 30)
    donor = _make_job(
        session, assembly, customer, discarded_at=datetime(2026, 8, 20, 12, 0)
    )
    _audit(session, donor, reviewed_at=reviewed_at)
    new_job = _make_job(session, assembly, customer)

    outcome = carry_forward_second_ops(session, new_job, _identity(assembly.id))

    assert outcome.kind is CarryForwardKind.copied
    assert outcome.donor_job_id == donor.id
    assert outcome.line_count == 3
    assert len(_lines_for(session, new_job.id)) == 3
    assert new_job.second_ops_unexpected_inclusions == "solder bridge on U4"
    # reviewed_at is copied verbatim, not restamped: the audit happened when it happened.
    assert new_job.second_ops_reviewed_at == reviewed_at


def test_copied_lines_preserve_line_order_and_every_field(session: Session):
    assembly, customer = _seed_assembly_and_customer(session)
    donor = _make_job(
        session, assembly, customer, discarded_at=datetime(2026, 8, 20, 12, 0)
    )
    _audit(session, donor, reviewed_at=datetime(2026, 8, 1), line_count=4)
    new_job = _make_job(session, assembly, customer)

    carry_forward_second_ops(session, new_job, _identity(assembly.id))

    donor_lines = _lines_for(session, donor.id)
    copied_lines = _lines_for(session, new_job.id)
    assert [line.line_order for line in copied_lines] == [0, 1, 2, 3]
    for donor_line, copied_line in zip(donor_lines, copied_lines):
        for field in (
            "find_number",
            "component_part_number",
            "per_board_count",
            "ref_des",
            "description",
            "mount_type",
            "quantity_needed",
            "quantity_on_hand",
        ):
            assert getattr(copied_line, field) == getattr(donor_line, field)


def test_later_discarded_donor_wins(session: Session):
    assembly, customer = _seed_assembly_and_customer(session)
    older = _make_job(
        session, assembly, customer, discarded_at=datetime(2026, 8, 10, 8, 0)
    )
    _audit(session, older, reviewed_at=datetime(2026, 8, 1), note="older", line_count=1)
    newer = _make_job(
        session, assembly, customer, discarded_at=datetime(2026, 8, 20, 8, 0)
    )
    _audit(session, newer, reviewed_at=datetime(2026, 8, 2), note="newer", line_count=2)
    new_job = _make_job(session, assembly, customer)

    outcome = carry_forward_second_ops(session, new_job, _identity(assembly.id))

    assert outcome.donor_job_id == newer.id
    assert new_job.second_ops_unexpected_inclusions == "newer"


def test_tie_on_discarded_at_is_broken_by_higher_id(session: Session):
    assembly, customer = _seed_assembly_and_customer(session)
    discarded_at = datetime(2026, 8, 20, 8, 0)
    first = _make_job(session, assembly, customer, discarded_at=discarded_at)
    _audit(session, first, reviewed_at=datetime(2026, 8, 1), note="first", line_count=1)
    second = _make_job(session, assembly, customer, discarded_at=discarded_at)
    _audit(session, second, reviewed_at=datetime(2026, 8, 2), note="second", line_count=1)
    new_job = _make_job(session, assembly, customer)

    outcome = carry_forward_second_ops(session, new_job, _identity(assembly.id))

    assert outcome.donor_job_id == max(first.id, second.id)
    assert new_job.second_ops_unexpected_inclusions == "second"


def test_discarded_but_never_audited_donor_is_not_copied(session: Session):
    assembly, customer = _seed_assembly_and_customer(session)
    _make_job(session, assembly, customer, discarded_at=datetime(2026, 8, 20, 8, 0))
    new_job = _make_job(session, assembly, customer)

    outcome = carry_forward_second_ops(session, new_job, _identity(assembly.id))

    assert outcome.kind is CarryForwardKind.skipped_no_donor
    assert new_job.second_ops_reviewed_at is None


def test_active_audited_job_is_not_a_donor(session: Session):
    """Only discarded rows donate — an active twin is a collision, not a source."""
    assembly, customer = _seed_assembly_and_customer(session)
    active = _make_job(session, assembly, customer)
    _audit(session, active, reviewed_at=datetime(2026, 8, 1))
    new_job = _make_job(session, assembly, customer)

    outcome = carry_forward_second_ops(session, new_job, _identity(assembly.id))

    assert outcome.kind is CarryForwardKind.skipped_no_donor


def test_donor_with_different_identity_is_ignored(session: Session):
    assembly, customer = _seed_assembly_and_customer(session)
    other_donor = _make_job(
        session,
        assembly,
        customer,
        split_suffix="-1par",
        discarded_at=datetime(2026, 8, 20, 8, 0),
    )
    _audit(session, other_donor, reviewed_at=datetime(2026, 8, 1))
    new_job = _make_job(session, assembly, customer)

    outcome = carry_forward_second_ops(session, new_job, _identity(assembly.id))

    assert outcome.kind is CarryForwardKind.skipped_no_donor


def test_operator_discarded_donor_is_copied(session: Session):
    """Cause of discard is not considered (Decision 15) — there is no discard_cause."""
    assembly, customer = _seed_assembly_and_customer(session)
    donor = _make_job(
        session, assembly, customer, discarded_at=datetime(2026, 8, 20, 8, 0)
    )
    _audit(session, donor, reviewed_at=datetime(2026, 8, 1))
    new_job = _make_job(session, assembly, customer)

    outcome = carry_forward_second_ops(session, new_job, _identity(assembly.id))

    assert outcome.kind is CarryForwardKind.copied


def test_no_donor_writes_nothing_and_logs_nothing(session: Session, caplog):
    assembly, customer = _seed_assembly_and_customer(session)
    new_job = _make_job(session, assembly, customer)

    with caplog.at_level(logging.INFO, logger="backend.app.transform"):
        outcome = carry_forward_second_ops(session, new_job, _identity(assembly.id))

    assert outcome.kind is CarryForwardKind.skipped_no_donor
    assert _lines_for(session, new_job.id) == []
    carry_forward_records = [
        record
        for record in caplog.records
        if "carry_forward" in record.getMessage()
    ]
    assert carry_forward_records == []


def test_donor_is_not_mutated(session: Session):
    assembly, customer = _seed_assembly_and_customer(session)
    reviewed_at = datetime(2026, 8, 1, 9, 30)
    donor = _make_job(
        session, assembly, customer, discarded_at=datetime(2026, 8, 20, 8, 0)
    )
    _audit(session, donor, reviewed_at=reviewed_at)
    new_job = _make_job(session, assembly, customer)

    carry_forward_second_ops(session, new_job, _identity(assembly.id))

    assert donor.second_ops_reviewed_at == reviewed_at
    assert donor.second_ops_unexpected_inclusions == "solder bridge on U4"
    assert len(_lines_for(session, donor.id)) == 3


# ---------------------------------------------------------------------------
# Idempotency guard
# ---------------------------------------------------------------------------


def test_second_call_is_skipped_and_warns(session: Session, caplog):
    assembly, customer = _seed_assembly_and_customer(session)
    donor = _make_job(
        session, assembly, customer, discarded_at=datetime(2026, 8, 20, 8, 0)
    )
    _audit(session, donor, reviewed_at=datetime(2026, 8, 1))
    new_job = _make_job(session, assembly, customer)

    first = carry_forward_second_ops(session, new_job, _identity(assembly.id))
    with caplog.at_level(logging.WARNING, logger="backend.app.transform"):
        second = carry_forward_second_ops(session, new_job, _identity(assembly.id))

    assert first.kind is CarryForwardKind.copied
    assert second.kind is CarryForwardKind.skipped_already_populated
    # No duplicated lines — the guard returns before reading the donor.
    assert len(_lines_for(session, new_job.id)) == 3
    assert any(
        "carry_forward.already_populated" in record.getMessage()
        and record.levelno == logging.WARNING
        for record in caplog.records
    )


# ---------------------------------------------------------------------------
# Call site — transform_staging_row
# ---------------------------------------------------------------------------


def _staging_row(session: Session, batch: ImportBatch, **overrides) -> ImportStagingRow:
    defaults = dict(
        batch_id=batch.id,
        source_row_number=2,
        raw_job=f"{PART_NUMBER}\nNEW",
        raw_qty="10",
        raw_customer="ACME Aerospace",
    )
    defaults.update(overrides)
    row = ImportStagingRow(**defaults)
    session.add(row)
    session.flush()
    return row


def test_insert_branch_carries_the_record_forward(
    session: Session, open_batch: ImportBatch
):
    assembly, customer = _seed_assembly_and_customer(session)
    reviewed_at = datetime(2026, 8, 1, 9, 30)
    donor = _make_job(
        session, assembly, customer, discarded_at=datetime(2026, 8, 20, 8, 0)
    )
    _audit(session, donor, reviewed_at=reviewed_at)
    session.commit()

    outcome = transform_staging_row(session, _staging_row(session, open_batch))

    assert outcome.action == "inserted"
    assert outcome.job.id != donor.id
    assert len(_lines_for(session, outcome.job.id)) == 3
    assert outcome.job.second_ops_reviewed_at == reviewed_at


def test_update_branch_does_not_run_carry_forward(
    session: Session, open_batch: ImportBatch
):
    """An updated job keeps its own record by definition."""
    assembly, customer = _seed_assembly_and_customer(session)
    donor = _make_job(
        session, assembly, customer, discarded_at=datetime(2026, 8, 20, 8, 0)
    )
    _audit(session, donor, reviewed_at=datetime(2026, 8, 1))
    existing = _make_job(session, assembly, customer)
    session.commit()

    outcome = transform_staging_row(session, _staging_row(session, open_batch))

    assert outcome.action == "updated"
    assert outcome.job.id == existing.id
    assert _lines_for(session, existing.id) == []
    assert existing.second_ops_reviewed_at is None


def test_donor_swept_in_the_same_batch_is_unreachable_by_construction(
    session: Session, open_batch: ImportBatch
):
    """Pins the Stage ordering: sweep is Stage 6b, after the Stage 5 transform loop.

    A job swept in batch N can only donate to an insert in batch N+1, so no
    intra-batch flush ordering has to be arranged.
    """
    import inspect

    from backend.app import ingest as ingest_module

    source = inspect.getsource(ingest_module.run_stages_4_to_6)
    transform_position = source.index("transform_staging_row")
    sweep_position = source.index("sweep_missing_planned_jobs")
    assert transform_position < sweep_position
