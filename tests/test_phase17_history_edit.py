"""Backend tests for Phase 17 Patch 01: History Inspect — Edit (discrete identity fields).

Covers:
  - Service-layer edit_history_job: happy paths for each editable field category.
  - Service-layer edit_history_job: error paths (not found, not editable, validation, collision).
  - Service-layer parse_positive_int / find_active_job_with_identity helpers.
  - API endpoint PATCH /api/jobs/{job_id}/history-edit: happy path, 404, 409, 422.
"""
from __future__ import annotations

from datetime import UTC, date, datetime

import pytest
from fastapi import status

from backend.app.models import Assembly, BuildQualifier, BuildType, Customer, Job, JobStatus
from backend.app.schemas import HistoryJobEditRequest
from backend.app.services.jobs import (
    JobEditIdentityCollisionError,
    JobEditNotEditableError,
    JobEditNotFoundError,
    JobEditValidationError,
    edit_history_job,
    find_active_job_with_identity,
    identity_key_for_job,
    parse_positive_int,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_assembly(session, part_number: str) -> Assembly:
    from sqlalchemy import select
    existing = session.execute(
        select(Assembly).where(Assembly.part_number == part_number)
    ).scalar_one_or_none()
    if existing:
        return existing
    a = Assembly(part_number=part_number)
    session.add(a)
    session.flush()
    return a


def _make_customer(session, name: str) -> Customer:
    from sqlalchemy import select
    existing = session.execute(
        select(Customer).where(Customer.name == name)
    ).scalar_one_or_none()
    if existing:
        return existing
    c = Customer(name=name)
    session.add(c)
    session.flush()
    return c


def _make_shipped_job(
    session,
    *,
    part_number: str = "HEDIT-001",
    customer_name: str = "HistCo",
    build_type: BuildType = BuildType.new,
    quantity: int = 10,
    shipped_at: date = date(2026, 1, 15),
    **overrides,
) -> Job:
    assembly = _make_assembly(session, part_number)
    customer = _make_customer(session, customer_name)
    job = Job(
        assembly_id=assembly.id,
        customer_id=customer.id,
        build_type=build_type,
        quantity=quantity,
        status=JobStatus.shipped,
        shipped_at=shipped_at,
        **overrides,
    )
    session.add(job)
    session.flush()
    return job


def _edit_request(**kwargs) -> HistoryJobEditRequest:
    """Build a HistoryJobEditRequest; defaults to reason='test edit' and raw_qty='5'."""
    kwargs.setdefault("reason", "test edit")
    kwargs.setdefault("raw_qty", "5")
    return HistoryJobEditRequest(**kwargs)


# ---------------------------------------------------------------------------
# Helper unit tests
# ---------------------------------------------------------------------------

class TestParsePositiveInt:
    def test_parse_positive_int_valid(self):
        assert parse_positive_int("10") == 10

    def test_parse_positive_int_strips_whitespace(self):
        assert parse_positive_int("  7  ") == 7

    def test_parse_positive_int_zero_returns_none(self):
        assert parse_positive_int("0") is None

    def test_parse_positive_int_negative_returns_none(self):
        assert parse_positive_int("-5") is None

    def test_parse_positive_int_non_numeric_returns_none(self):
        assert parse_positive_int("abc") is None

    def test_parse_positive_int_empty_returns_none(self):
        assert parse_positive_int("") is None


class TestFindActiveJobWithIdentity:
    def test_find_returns_collider_id(self, session):
        j1 = _make_shipped_job(session, part_number="COLL-001")
        j2 = _make_shipped_job(session, part_number="COLL-001",
                               customer_name="OtherCo")
        key = identity_key_for_job(j1)
        result = find_active_job_with_identity(session, key=key, exclude_job_id=j1.id)
        assert result == j2.id

    def test_find_excludes_self(self, session):
        j = _make_shipped_job(session, part_number="SELF-001")
        key = identity_key_for_job(j)
        result = find_active_job_with_identity(session, key=key, exclude_job_id=j.id)
        assert result is None

    def test_find_excludes_discarded(self, session):
        j1 = _make_shipped_job(session, part_number="DISC-COLL-001")
        j2 = _make_shipped_job(session, part_number="DISC-COLL-001",
                               customer_name="OtherCo")
        j2.discarded_at = datetime.now(UTC).replace(tzinfo=None)
        session.flush()
        key = identity_key_for_job(j1)
        result = find_active_job_with_identity(session, key=key, exclude_job_id=j1.id)
        assert result is None


# ---------------------------------------------------------------------------
# Service-layer: edit_history_job
# ---------------------------------------------------------------------------

class TestEditHistoryJobService:
    def test_edit_qty_updates_quantity(self, session):
        job = _make_shipped_job(session, quantity=10)
        req = _edit_request(raw_qty="25")
        returned = edit_history_job(session, job.id, req)
        assert returned.quantity == 25

    def test_edit_customer_updates_customer(self, session):
        job = _make_shipped_job(session, customer_name="OldCo")
        req = _edit_request(raw_customer="NewCo")
        returned = edit_history_job(session, job.id, req)
        assert returned.customer.name == "NewCo"

    def test_edit_shipped_date_updates_shipped_at(self, session):
        job = _make_shipped_job(session, shipped_at=date(2026, 1, 1))
        req = _edit_request(raw_shipped="03/15/2026")
        returned = edit_history_job(session, job.id, req)
        assert returned.shipped_at == date(2026, 3, 15)

    def test_edit_shipped_date_does_not_touch_ever_shipped_at(self, session):
        """INV-S3: ever_shipped_at must not be overwritten."""
        ever = date(2025, 12, 1)
        job = _make_shipped_job(session, shipped_at=date(2026, 1, 1), ever_shipped_at=ever)
        req = _edit_request(raw_shipped="06/01/2026")
        returned = edit_history_job(session, job.id, req)
        assert returned.ever_shipped_at == ever

    def test_edit_part_number_updates_assembly(self, session):
        job = _make_shipped_job(session, part_number="OLD-001")
        _make_assembly(session, "NEW-999")
        req = _edit_request(part_number="NEW-999")
        returned = edit_history_job(session, job.id, req)
        assert returned.assembly.part_number == "NEW-999"

    def test_edit_build_type_updates_build_type(self, session):
        job = _make_shipped_job(session, build_type=BuildType.new)
        req = _edit_request(build_type="ronc")
        returned = edit_history_job(session, job.id, req)
        assert returned.build_type == BuildType.ronc

    def test_edit_build_type_case_insensitive(self, session):
        job = _make_shipped_job(session, build_type=BuildType.new)
        req = _edit_request(build_type="ROWC")
        returned = edit_history_job(session, job.id, req)
        assert returned.build_type == BuildType.rowc

    def test_edit_split_suffix_sets_value(self, session):
        job = _make_shipped_job(session)
        req = _edit_request(split_suffix="-1a")
        returned = edit_history_job(session, job.id, req)
        assert returned.split_suffix == "-1a"

    def test_edit_split_suffix_blank_clears_to_null(self, session):
        job = _make_shipped_job(session, split_suffix="-1a")
        req = _edit_request(split_suffix="")
        returned = edit_history_job(session, job.id, req)
        assert returned.split_suffix is None

    def test_edit_build_qualifier_sets_value(self, session):
        job = _make_shipped_job(session)
        req = _edit_request(build_qualifier="rwk")
        returned = edit_history_job(session, job.id, req)
        assert returned.build_qualifier == BuildQualifier.rwk

    def test_edit_build_qualifier_blank_clears_to_null(self, session):
        job = _make_shipped_job(session, build_qualifier=BuildQualifier.rwk)
        req = _edit_request(build_qualifier="")
        returned = edit_history_job(session, job.id, req)
        assert returned.build_qualifier is None

    def test_edit_not_found_raises(self, session):
        req = _edit_request(raw_qty="5")
        with pytest.raises(JobEditNotFoundError) as exc_info:
            edit_history_job(session, 999_999, req)
        assert exc_info.value.job_id == 999_999

    def test_edit_discarded_raises_not_editable(self, session):
        job = _make_shipped_job(session)
        job.discarded_at = datetime.now(UTC).replace(tzinfo=None)
        session.flush()
        req = _edit_request(raw_qty="5")
        with pytest.raises(JobEditNotEditableError) as exc_info:
            edit_history_job(session, job.id, req)
        assert exc_info.value.kind == "discarded"

    def test_edit_non_shipped_raises_not_editable(self, session):
        job = _make_shipped_job(session)
        job.status = JobStatus.planned
        session.flush()
        req = _edit_request(raw_qty="5")
        with pytest.raises(JobEditNotEditableError) as exc_info:
            edit_history_job(session, job.id, req)
        assert exc_info.value.kind == "not_shipped"

    def test_edit_invalid_qty_raises_validation_error(self, session):
        job = _make_shipped_job(session)
        req = _edit_request(raw_qty="not-a-number")
        with pytest.raises(JobEditValidationError) as exc_info:
            edit_history_job(session, job.id, req)
        assert exc_info.value.field == "raw_qty"

    def test_edit_zero_qty_raises_validation_error(self, session):
        job = _make_shipped_job(session)
        req = _edit_request(raw_qty="0")
        with pytest.raises(JobEditValidationError) as exc_info:
            edit_history_job(session, job.id, req)
        assert exc_info.value.field == "raw_qty"

    def test_edit_blank_customer_raises_validation_error(self, session):
        job = _make_shipped_job(session)
        req = _edit_request(raw_customer="   ")
        with pytest.raises(JobEditValidationError) as exc_info:
            edit_history_job(session, job.id, req)
        assert exc_info.value.field == "raw_customer"

    def test_edit_invalid_shipped_date_raises_validation_error(self, session):
        job = _make_shipped_job(session)
        req = _edit_request(raw_shipped="not-a-date")
        with pytest.raises(JobEditValidationError) as exc_info:
            edit_history_job(session, job.id, req)
        assert exc_info.value.field == "raw_shipped"

    def test_edit_empty_part_number_raises_validation_error(self, session):
        job = _make_shipped_job(session)
        req = _edit_request(part_number="")
        with pytest.raises(JobEditValidationError) as exc_info:
            edit_history_job(session, job.id, req)
        assert exc_info.value.field == "part_number"

    def test_edit_empty_build_type_raises_validation_error(self, session):
        job = _make_shipped_job(session)
        req = _edit_request(build_type="")
        with pytest.raises(JobEditValidationError) as exc_info:
            edit_history_job(session, job.id, req)
        assert exc_info.value.field == "build_type"

    def test_edit_invalid_build_type_raises_validation_error(self, session):
        job = _make_shipped_job(session)
        req = _edit_request(build_type="bogus")
        with pytest.raises(JobEditValidationError) as exc_info:
            edit_history_job(session, job.id, req)
        assert exc_info.value.field == "build_type"

    def test_edit_invalid_build_qualifier_raises_validation_error(self, session):
        job = _make_shipped_job(session)
        req = _edit_request(build_qualifier="bogus")
        with pytest.raises(JobEditValidationError) as exc_info:
            edit_history_job(session, job.id, req)
        assert exc_info.value.field == "build_qualifier"

    def test_edit_identity_collision_raises(self, session):
        """Editing part_number to match another active job's identity must raise."""
        j1 = _make_shipped_job(session, part_number="COL-A")
        j2 = _make_shipped_job(session, part_number="COL-B")
        req = _edit_request(part_number="COL-A")
        with pytest.raises(JobEditIdentityCollisionError) as exc_info:
            edit_history_job(session, j2.id, req)
        assert exc_info.value.colliding_job_id == j1.id


# ---------------------------------------------------------------------------
# Schema validation
# ---------------------------------------------------------------------------

class TestHistoryJobEditRequestSchema:
    def test_no_editable_fields_raises(self):
        with pytest.raises(Exception):
            HistoryJobEditRequest(reason="test")

    def test_one_ship_time_field_passes(self):
        req = HistoryJobEditRequest(reason="test", raw_qty="5")
        assert req.raw_qty == "5"

    def test_one_identity_field_passes(self):
        req = HistoryJobEditRequest(reason="test", part_number="X-001")
        assert req.part_number == "X-001"

    def test_raw_job_rejected_extra_forbid(self):
        """Stale clients sending raw_job must get 422, not a silent drop."""
        with pytest.raises(Exception):
            HistoryJobEditRequest(reason="test", raw_job="OLD-001 NEW", raw_qty="5")

    def test_empty_reason_raises(self):
        with pytest.raises(Exception):
            HistoryJobEditRequest(reason="", raw_qty="5")

    def test_reason_too_long_raises(self):
        with pytest.raises(Exception):
            HistoryJobEditRequest(reason="x" * 501, raw_qty="5")


# ---------------------------------------------------------------------------
# API endpoint tests: PATCH /api/jobs/{job_id}/history-edit
# ---------------------------------------------------------------------------

class TestEditHistoryJobEndpoint:
    def test_edit_qty_happy_path(self, client, session):
        job = _make_shipped_job(session)
        session.commit()

        resp = client.patch(
            f"/api/jobs/{job.id}/history-edit",
            json={"raw_qty": "99", "reason": "fix qty"},
        )
        assert resp.status_code == status.HTTP_200_OK
        assert resp.json()["quantity"] == 99

    def test_edit_customer_happy_path(self, client, session):
        job = _make_shipped_job(session, customer_name="OldCo")
        session.commit()

        resp = client.patch(
            f"/api/jobs/{job.id}/history-edit",
            json={"raw_customer": "NewCustomer", "reason": "wrong customer"},
        )
        assert resp.status_code == status.HTTP_200_OK
        assert resp.json()["customer"]["name"] == "NewCustomer"

    def test_edit_shipped_date_happy_path(self, client, session):
        job = _make_shipped_job(session, shipped_at=date(2026, 1, 1))
        session.commit()

        resp = client.patch(
            f"/api/jobs/{job.id}/history-edit",
            json={"raw_shipped": "2026-06-15", "reason": "correct date"},
        )
        assert resp.status_code == status.HTTP_200_OK
        assert resp.json()["shipped_at"] == "2026-06-15"

    def test_edit_part_number_happy_path(self, client, session):
        job = _make_shipped_job(session, part_number="OLD-PN")
        session.commit()

        resp = client.patch(
            f"/api/jobs/{job.id}/history-edit",
            json={"part_number": "NEW-PN", "reason": "wrong part"},
        )
        assert resp.status_code == status.HTTP_200_OK
        assert resp.json()["assembly"]["part_number"] == "NEW-PN"

    def test_edit_not_found_returns_404(self, client):
        resp = client.patch(
            "/api/jobs/999999/history-edit",
            json={"raw_qty": "5", "reason": "test"},
        )
        assert resp.status_code == status.HTTP_404_NOT_FOUND

    def test_edit_non_shipped_returns_409_with_kind(self, client, session):
        job = _make_shipped_job(session)
        job.status = JobStatus.planned
        session.commit()

        resp = client.patch(
            f"/api/jobs/{job.id}/history-edit",
            json={"raw_qty": "5", "reason": "test"},
        )
        assert resp.status_code == status.HTTP_409_CONFLICT
        detail = resp.json()["detail"]
        assert detail["kind"] == "not_shipped"

    def test_edit_discarded_returns_409_with_kind(self, client, session):
        job = _make_shipped_job(session)
        job.discarded_at = datetime.now(UTC).replace(tzinfo=None)
        session.commit()

        resp = client.patch(
            f"/api/jobs/{job.id}/history-edit",
            json={"raw_qty": "5", "reason": "test"},
        )
        assert resp.status_code == status.HTTP_409_CONFLICT
        detail = resp.json()["detail"]
        assert detail["kind"] == "discarded"

    def test_edit_invalid_qty_returns_422_with_field(self, client, session):
        job = _make_shipped_job(session)
        session.commit()

        resp = client.patch(
            f"/api/jobs/{job.id}/history-edit",
            json={"raw_qty": "nope", "reason": "test"},
        )
        assert resp.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY
        detail = resp.json()["detail"]
        assert detail["field"] == "raw_qty"

    def test_edit_no_editable_fields_returns_422(self, client, session):
        job = _make_shipped_job(session)
        session.commit()

        resp = client.patch(
            f"/api/jobs/{job.id}/history-edit",
            json={"reason": "test"},
        )
        assert resp.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY

    def test_edit_raw_job_rejected_by_extra_forbid(self, client, session):
        """Stale clients sending raw_job must receive a 422, not a silent drop."""
        job = _make_shipped_job(session)
        session.commit()

        resp = client.patch(
            f"/api/jobs/{job.id}/history-edit",
            json={"raw_job": "OLD-001 NEW", "reason": "test"},
        )
        assert resp.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY

    def test_edit_identity_collision_returns_409_with_colliding_id(self, client, session):
        j1 = _make_shipped_job(session, part_number="ECOL-A")
        j2 = _make_shipped_job(session, part_number="ECOL-B")
        session.commit()

        resp = client.patch(
            f"/api/jobs/{j2.id}/history-edit",
            json={"part_number": "ECOL-A", "reason": "test"},
        )
        assert resp.status_code == status.HTTP_409_CONFLICT
        detail = resp.json()["detail"]
        assert detail["colliding_job_id"] == j1.id



# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_assembly(session, part_number: str) -> Assembly:
    from sqlalchemy import select
    existing = session.execute(
        select(Assembly).where(Assembly.part_number == part_number)
    ).scalar_one_or_none()
    if existing:
        return existing
    a = Assembly(part_number=part_number)
    session.add(a)
    session.flush()
    return a


def _make_customer(session, name: str) -> Customer:
    from sqlalchemy import select
    existing = session.execute(
        select(Customer).where(Customer.name == name)
    ).scalar_one_or_none()
    if existing:
        return existing
    c = Customer(name=name)
    session.add(c)
    session.flush()
    return c


def _make_shipped_job(
    session,
    *,
    part_number: str = "HEDIT-001",
    customer_name: str = "HistCo",
    build_type: BuildType = BuildType.new,
    quantity: int = 10,
    shipped_at: date = date(2026, 1, 15),
    **overrides,
) -> Job:
    assembly = _make_assembly(session, part_number)
    customer = _make_customer(session, customer_name)
    job = Job(
        assembly_id=assembly.id,
        customer_id=customer.id,
        build_type=build_type,
        quantity=quantity,
        status=JobStatus.shipped,
        shipped_at=shipped_at,
        **overrides,
    )
    session.add(job)
    session.flush()
    return job


def _edit_request(**kwargs) -> HistoryJobEditRequest:
    """Build a HistoryJobEditRequest; defaults to reason='test edit' and raw_qty='5'."""
    kwargs.setdefault("reason", "test edit")
    kwargs.setdefault("raw_qty", "5")
    return HistoryJobEditRequest(**kwargs)


# ---------------------------------------------------------------------------
# Helper unit tests
# ---------------------------------------------------------------------------

class TestParsePositiveInt:
    def test_parse_positive_int_valid(self):
        assert parse_positive_int("10") == 10

    def test_parse_positive_int_strips_whitespace(self):
        assert parse_positive_int("  7  ") == 7

    def test_parse_positive_int_zero_returns_none(self):
        assert parse_positive_int("0") is None

    def test_parse_positive_int_negative_returns_none(self):
        assert parse_positive_int("-5") is None

    def test_parse_positive_int_non_numeric_returns_none(self):
        assert parse_positive_int("abc") is None

    def test_parse_positive_int_empty_returns_none(self):
        assert parse_positive_int("") is None


class TestFindActiveJobWithIdentity:
    def test_find_returns_collider_id(self, session):
        j1 = _make_shipped_job(session, part_number="COLL-001")
        j2 = _make_shipped_job(session, part_number="COLL-001",
                               customer_name="OtherCo")
        # Give j1 and j2 different customer names so they're separate rows,
        # but same identity key (same assembly, build_type, no suffix/ref).
        key = identity_key_for_job(j1)
        result = find_active_job_with_identity(session, key=key, exclude_job_id=j1.id)
        assert result == j2.id

    def test_find_excludes_self(self, session):
        j = _make_shipped_job(session, part_number="SELF-001")
        key = identity_key_for_job(j)
        result = find_active_job_with_identity(session, key=key, exclude_job_id=j.id)
        assert result is None

    def test_find_excludes_discarded(self, session):
        j1 = _make_shipped_job(session, part_number="DISC-COLL-001")
        j2 = _make_shipped_job(session, part_number="DISC-COLL-001",
                               customer_name="OtherCo")
        j2.discarded_at = datetime.now(UTC).replace(tzinfo=None)
        session.flush()
        key = identity_key_for_job(j1)
        result = find_active_job_with_identity(session, key=key, exclude_job_id=j1.id)
        assert result is None



