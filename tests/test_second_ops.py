"""2nd OPS service, validation and endpoints (Phase 22 Part 2).

Trust boundary under test: the client parses the Audit BOM paste and maps the
columns; the server re-validates count, per-field widths and caps independently.
"""
from __future__ import annotations

from datetime import datetime

import pytest
from sqlalchemy import event, select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from backend.app.config import Settings, get_settings
from backend.app.models import (
    Assembly,
    BuildType,
    Customer,
    Job,
    JobSecondOpsLine,
    JobStatus,
)
from backend.app.schemas import JobReadExpanded, SecondOpsWriteRequest
from backend.app.services import second_ops as second_ops_service
from backend.app.services.second_ops import (
    SecondOpsNotFound,
    SecondOpsRejection,
    SecondOpsWriteFailure,
    ValidatedSecondOps,
    get_second_ops_record,
    load_second_ops_summaries,
    replace_second_ops,
    validate_second_ops_payload,
)


def _settings(**overrides) -> Settings:
    base = dict(
        second_ops_max_lines=500,
        second_ops_note_max_chars=4000,
        second_ops_preview_lines=3,
    )
    base.update(overrides)
    return Settings(**base)


def _line_payload(index: int = 0) -> dict:
    return {
        "find_number": str(index + 1),
        "component_part_number": f"CMP-{index}",
        "per_board_count": "2",
        "ref_des": f"C{index}, C{index + 10}",
        "description": f"CAP {index}",
        "mount_type": "SMT",
        "quantity_needed": "40",
        "quantity_on_hand": "500",
    }


def _make_job(session: Session, *, part_number="B142006", **overrides) -> Job:
    assembly = Assembly(part_number=part_number)
    session.add(assembly)
    session.flush()
    customer = session.scalars(
        select(Customer).where(Customer.name == "ACME")
    ).first()
    if customer is None:
        customer = Customer(name="ACME")
        session.add(customer)
        session.flush()
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


def _add_lines(session: Session, job: Job, count: int) -> None:
    for index in range(count):
        session.add(
            JobSecondOpsLine(job_id=job.id, line_order=index, **_line_payload(index))
        )
    session.flush()


def _lines_for(session: Session, job_id: int) -> list[JobSecondOpsLine]:
    return list(
        session.scalars(
            select(JobSecondOpsLine)
            .where(JobSecondOpsLine.job_id == job_id)
            .order_by(JobSecondOpsLine.line_order)
        ).all()
    )


class _QueryCounter:
    """Counts statements issued on an engine within the context block."""

    def __init__(self, engine):
        self.engine = engine
        self.statements: list[str] = []

    def __enter__(self):
        event.listen(self.engine, "before_cursor_execute", self._record)
        return self

    def __exit__(self, *exc):
        event.remove(self.engine, "before_cursor_execute", self._record)
        return False

    def _record(self, conn, cursor, statement, parameters, context, executemany):
        self.statements.append(statement)

    def __len__(self) -> int:
        return len(self.statements)


# ---------------------------------------------------------------------------
# validate_second_ops_payload — bounds and the single strip
# ---------------------------------------------------------------------------


def test_validation_accepts_a_payload_within_bounds():
    payload = SecondOpsWriteRequest(lines=[_line_payload(0)], unexpected_inclusions="ok")
    validated = validate_second_ops_payload(payload, _settings())
    assert isinstance(validated, ValidatedSecondOps)
    assert len(validated.lines) == 1


def test_validation_rejects_line_count_over_the_setting():
    payload = SecondOpsWriteRequest(lines=[_line_payload(i) for i in range(4)])
    rejection = validate_second_ops_payload(payload, _settings(second_ops_max_lines=3))
    assert isinstance(rejection, SecondOpsRejection)
    assert rejection.field == "lines"


def test_validation_rejects_note_over_the_setting_measured_after_stripping():
    payload = SecondOpsWriteRequest(unexpected_inclusions="  " + "x" * 11 + "  ")
    rejection = validate_second_ops_payload(
        payload, _settings(second_ops_note_max_chars=10)
    )
    assert isinstance(rejection, SecondOpsRejection)
    assert rejection.field == "unexpected_inclusions"


def test_validation_accepts_a_note_whose_padding_pushes_it_over():
    """Length is measured AFTER stripping, so the padding is not counted."""
    payload = SecondOpsWriteRequest(unexpected_inclusions="  " + "x" * 10 + "  ")
    validated = validate_second_ops_payload(
        payload, _settings(second_ops_note_max_chars=10)
    )
    assert isinstance(validated, ValidatedSecondOps)
    assert validated.unexpected_inclusions == "x" * 10


def test_validation_owns_the_only_strip_and_nulls_a_blank_note():
    validated = validate_second_ops_payload(
        SecondOpsWriteRequest(unexpected_inclusions="   "), _settings()
    )
    assert isinstance(validated, ValidatedSecondOps)
    assert validated.unexpected_inclusions is None


def test_validation_carries_line_fields_verbatim():
    payload = SecondOpsWriteRequest(lines=[{"description": "  padded  "}])
    validated = validate_second_ops_payload(payload, _settings())
    assert isinstance(validated, ValidatedSecondOps)
    assert validated.lines[0].description == "  padded  "


# ---------------------------------------------------------------------------
# replace_second_ops — write semantics
# ---------------------------------------------------------------------------


def _validated(lines=(), note=None) -> ValidatedSecondOps:
    payload = SecondOpsWriteRequest(lines=list(lines), unexpected_inclusions=note)
    validated = validate_second_ops_payload(payload, _settings())
    assert isinstance(validated, ValidatedSecondOps)
    return validated


def test_replace_writes_lines_in_submission_order_and_stamps_reviewed_at(
    session: Session,
):
    job = _make_job(session)
    stamped = datetime(2026, 8, 28, 10, 0)

    record = replace_second_ops(
        session,
        job.id,
        _validated([_line_payload(0), _line_payload(1)]),
        _settings(),
        clock=lambda: stamped,
    )

    assert not isinstance(record, SecondOpsWriteFailure)
    assert [line.line_order for line in record.lines] == [0, 1]
    assert record.reviewed_at == stamped
    assert record.state == "recorded"


def test_replace_with_empty_lines_and_blank_note_is_not_applicable(session: Session):
    job = _make_job(session)

    record = replace_second_ops(session, job.id, _validated(note="   "), _settings())

    assert record.state == "not_applicable"
    # reviewed_at is stamped either way — not_applicable is not a return to unaudited.
    assert record.reviewed_at is not None
    assert record.unexpected_inclusions is None


def test_replace_is_a_whole_set_replace_not_a_merge(session: Session):
    job = _make_job(session)
    replace_second_ops(
        session, job.id, _validated([_line_payload(i) for i in range(3)]), _settings()
    )

    record = replace_second_ops(
        session, job.id, _validated([_line_payload(9)]), _settings()
    )

    assert len(record.lines) == 1
    assert [line.line_order for line in record.lines] == [0]
    assert record.lines[0].find_number == "10"


def test_replace_persists_the_stripped_note_without_stripping_again(session: Session):
    job = _make_job(session)

    record = replace_second_ops(
        session, job.id, _validated(note="  audit note  "), _settings()
    )

    assert record.unexpected_inclusions == "audit note"


def test_replace_persists_line_fields_verbatim(session: Session):
    """The note rule must not leak onto the transcription fields."""
    job = _make_job(session)

    record = replace_second_ops(
        session, job.id, _validated([{"description": "  leading spaces"}]), _settings()
    )

    assert record.lines[0].description == "  leading spaces"


# ---------------------------------------------------------------------------
# Write guard
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "overrides,expected_kind",
    [
        ({"status": JobStatus.shipped}, "shipped"),
        ({"discarded_at": datetime(2026, 8, 20)}, "discarded"),
        ({"superseded_at": datetime(2026, 8, 20)}, "superseded"),
    ],
)
def test_write_guard_rejects_non_planned_jobs(session: Session, overrides, expected_kind):
    job = _make_job(session, part_number=f"GUARD-{expected_kind}", **overrides)

    outcome = replace_second_ops(
        session, job.id, _validated([_line_payload(0)]), _settings()
    )

    assert isinstance(outcome, SecondOpsWriteFailure)
    assert outcome.kind == expected_kind
    assert _lines_for(session, job.id) == []


def test_write_guard_rejects_a_nonexistent_job(session: Session):
    outcome = replace_second_ops(session, 999_999, _validated(), _settings())
    assert isinstance(outcome, SecondOpsWriteFailure)
    assert outcome.kind == "not_found"


# ---------------------------------------------------------------------------
# Transaction integrity
# ---------------------------------------------------------------------------


def test_prior_lines_survive_an_insert_failure(session: Session, monkeypatch):
    """Delete and insert share one transaction."""
    job = _make_job(session)
    _add_lines(session, job, 3)
    job.second_ops_reviewed_at = datetime(2026, 8, 1)
    session.flush()

    def _exploding_add(instance):
        raise OperationalError("INSERT", {}, Exception("disk I/O error"))

    monkeypatch.setattr(session, "add", _exploding_add)
    outcome = replace_second_ops(
        session, job.id, _validated([_line_payload(0)]), _settings()
    )
    monkeypatch.undo()

    assert isinstance(outcome, SecondOpsWriteFailure)
    assert outcome.kind == "storage"
    assert len(_lines_for(session, job.id)) == 3


def test_unexpected_exception_propagates_rather_than_becoming_storage(
    session: Session, monkeypatch
):
    job = _make_job(session)

    def _exploding_add(instance):
        raise RuntimeError("not a database error")

    monkeypatch.setattr(session, "add", _exploding_add)
    with pytest.raises(RuntimeError):
        replace_second_ops(session, job.id, _validated([_line_payload(0)]), _settings())


# ---------------------------------------------------------------------------
# get_second_ops_record
# ---------------------------------------------------------------------------


def test_get_record_synthesises_for_a_never_audited_job(session: Session):
    job = _make_job(session)

    record = get_second_ops_record(session, job.id, _settings())

    assert not isinstance(record, SecondOpsNotFound)
    assert record.state == "unaudited"
    assert record.lines == []
    assert record.reviewed_at is None
    assert record.unexpected_inclusions is None


def test_get_record_returns_not_found_only_for_a_nonexistent_id(session: Session):
    assert isinstance(get_second_ops_record(session, 999_999, _settings()), SecondOpsNotFound)


@pytest.mark.parametrize(
    "overrides",
    [
        {"status": JobStatus.shipped},
        {"discarded_at": datetime(2026, 8, 20)},
    ],
)
def test_get_record_is_not_guarded_by_status(session: Session, overrides):
    job = _make_job(session, part_number=f"READ-{len(overrides)}{overrides}", **overrides)
    _add_lines(session, job, 2)
    job.second_ops_reviewed_at = datetime(2026, 8, 1)
    session.flush()

    record = get_second_ops_record(session, job.id, _settings())

    assert not isinstance(record, SecondOpsNotFound)
    assert len(record.lines) == 2


def test_get_record_returns_every_line_ordered_and_unbounded_by_preview_cap(
    session: Session,
):
    job = _make_job(session)
    _add_lines(session, job, 12)
    job.second_ops_reviewed_at = datetime(2026, 8, 1)
    session.flush()

    record = get_second_ops_record(session, job.id, _settings(second_ops_preview_lines=3))

    assert [line.line_order for line in record.lines] == list(range(12))


def test_get_record_limits_echo_live_settings(session: Session):
    job = _make_job(session)

    record = get_second_ops_record(
        session,
        job.id,
        _settings(second_ops_max_lines=10, second_ops_note_max_chars=99),
    )

    assert record.limits.max_lines == 10
    assert record.limits.note_max_chars == 99


# ---------------------------------------------------------------------------
# load_second_ops_summaries
# ---------------------------------------------------------------------------


def test_summaries_use_two_queries_regardless_of_page_size(session: Session, engine):
    jobs = [_make_job(session, part_number=f"PAGE-{i}") for i in range(8)]
    for job in jobs:
        _add_lines(session, job, 5)
        job.second_ops_reviewed_at = datetime(2026, 8, 1)
    session.flush()

    with _QueryCounter(engine) as counter:
        summaries = load_second_ops_summaries(session, jobs, 3)

    assert len(counter) == 2
    assert len(summaries) == 8


def test_summaries_truncate_per_job_not_globally(session: Session):
    """Asserts the PARTITION BY — a global LIMIT would starve the second job."""
    over_cap = _make_job(session, part_number="OVER")
    under_cap = _make_job(session, part_number="UNDER")
    _add_lines(session, over_cap, 10)
    _add_lines(session, under_cap, 2)
    for job in (over_cap, under_cap):
        job.second_ops_reviewed_at = datetime(2026, 8, 1)
    session.flush()

    summaries = load_second_ops_summaries(session, [over_cap, under_cap], 3)

    assert len(summaries[over_cap.id].preview) == 3
    assert summaries[over_cap.id].line_count == 10
    assert len(summaries[under_cap.id].preview) == 2
    assert summaries[under_cap.id].line_count == 2


def test_summary_preview_lines_carry_all_eight_fields_plus_identity(session: Session):
    """Enough to open the item modal without a second fetch."""
    job = _make_job(session)
    _add_lines(session, job, 1)
    job.second_ops_reviewed_at = datetime(2026, 8, 1)
    session.flush()

    preview_line = load_second_ops_summaries(session, [job], 3)[job.id].preview[0]

    assert preview_line.id > 0
    assert preview_line.line_order == 0
    assert preview_line.find_number == "1"
    assert preview_line.component_part_number == "CMP-0"
    assert preview_line.per_board_count == "2"
    assert preview_line.ref_des == "C0, C10"
    assert preview_line.description == "CAP 0"
    assert preview_line.mount_type == "SMT"
    assert preview_line.quantity_needed == "40"
    assert preview_line.quantity_on_hand == "500"


def test_summaries_include_every_passed_job_when_no_job_has_lines(session: Session):
    """Both lines-table queries return nothing and the mapping is still complete."""
    jobs = [_make_job(session, part_number=f"EMPTY-{i}") for i in range(3)]

    summaries = load_second_ops_summaries(session, jobs, 3)

    assert set(summaries) == {job.id for job in jobs}
    assert all(summary.state == "unaudited" for summary in summaries.values())
    assert all(summary.line_count == 0 for summary in summaries.values())


def test_zero_line_audited_job_is_not_applicable(session: Session):
    """The state came off the Job instance; no lines-table query could supply it."""
    job = _make_job(session)
    job.second_ops_reviewed_at = datetime(2026, 8, 1)
    session.flush()

    summary = load_second_ops_summaries(session, [job], 3)[job.id]

    assert summary.state == "not_applicable"
    assert summary.line_count == 0
    assert summary.has_unexpected_inclusions is False


def test_zero_line_job_with_a_note_is_recorded(session: Session):
    job = _make_job(session)
    job.second_ops_reviewed_at = datetime(2026, 8, 1)
    job.second_ops_unexpected_inclusions = "extra washer in kit"
    session.flush()

    summary = load_second_ops_summaries(session, [job], 3)[job.id]

    assert summary.state == "recorded"
    assert summary.has_unexpected_inclusions is True


def test_summaries_for_an_empty_page_is_an_empty_mapping(session: Session):
    assert load_second_ops_summaries(session, [], 3) == {}


# ---------------------------------------------------------------------------
# HTTP surface
# ---------------------------------------------------------------------------


def _override_settings(client, **overrides):
    client.app.dependency_overrides[get_settings] = lambda: _settings(**overrides)


def test_put_persists_lines_on_a_planned_job(client, session):
    job = _make_job(session)
    session.commit()

    response = client.put(
        f"/api/jobs/{job.id}/second-ops",
        json={"lines": [_line_payload(0), _line_payload(1)], "unexpected_inclusions": None},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["state"] == "recorded"
    assert [line["line_order"] for line in body["lines"]] == [0, 1]
    assert body["reviewed_at"] is not None


def test_put_on_a_shipped_job_is_409_and_writes_nothing(client, session):
    job = _make_job(session, status=JobStatus.shipped)
    session.commit()

    response = client.put(
        f"/api/jobs/{job.id}/second-ops", json={"lines": [_line_payload(0)]}
    )

    assert response.status_code == 409
    assert response.json()["detail"]["kind"] == "shipped"
    assert _lines_for(session, job.id) == []


def test_put_on_a_discarded_job_is_409(client, session):
    job = _make_job(session, discarded_at=datetime(2026, 8, 20))
    session.commit()

    response = client.put(
        f"/api/jobs/{job.id}/second-ops", json={"lines": [_line_payload(0)]}
    )

    assert response.status_code == 409
    assert response.json()["detail"]["kind"] == "discarded"


def test_put_on_a_nonexistent_job_is_404(client):
    response = client.put("/api/jobs/999999/second-ops", json={"lines": []})
    assert response.status_code == 404


def test_put_over_max_lines_is_422_and_writes_zero_rows(client, session):
    job = _make_job(session)
    session.commit()
    _override_settings(client, second_ops_max_lines=2)

    response = client.put(
        f"/api/jobs/{job.id}/second-ops",
        json={"lines": [_line_payload(i) for i in range(3)]},
    )

    assert response.status_code == 422
    assert response.json()["detail"]["field"] == "lines"
    assert _lines_for(session, job.id) == []


def test_put_with_a_field_over_its_column_width_is_422(client, session):
    job = _make_job(session)
    session.commit()

    response = client.put(
        f"/api/jobs/{job.id}/second-ops",
        json={"lines": [{"description": "d" * 256}]},
    )

    assert response.status_code == 422
    assert _lines_for(session, job.id) == []


def test_put_with_a_3000_character_ref_des_is_422(client, session):
    """Asserts the varchar(2048) bound reaches validation. Text would admit it."""
    job = _make_job(session)
    session.commit()

    response = client.put(
        f"/api/jobs/{job.id}/second-ops",
        json={"lines": [{"ref_des": "R" * 3000}]},
    )

    assert response.status_code == 422
    assert _lines_for(session, job.id) == []


def test_put_with_an_over_long_note_is_422(client, session):
    job = _make_job(session)
    session.commit()
    _override_settings(client, second_ops_note_max_chars=10)

    response = client.put(
        f"/api/jobs/{job.id}/second-ops",
        json={"lines": [], "unexpected_inclusions": "n" * 11},
    )

    assert response.status_code == 422
    assert response.json()["detail"]["field"] == "unexpected_inclusions"


def test_put_rejects_unknown_body_keys(client, session):
    job = _make_job(session)
    session.commit()

    response = client.put(
        f"/api/jobs/{job.id}/second-ops",
        json={"lines": [], "state": "recorded"},
    )

    assert response.status_code == 422


def test_storage_failure_maps_to_500(client, session, monkeypatch):
    job = _make_job(session)
    session.commit()
    monkeypatch.setattr(
        second_ops_service,
        "replace_second_ops",
        lambda *args, **kwargs: SecondOpsWriteFailure(
            kind="storage", message="The audit could not be saved."
        ),
    )

    response = client.put(f"/api/jobs/{job.id}/second-ops", json={"lines": []})

    assert response.status_code == 500
    assert response.json()["detail"]["kind"] == "storage"


def test_get_on_a_never_audited_job_is_200_not_404(client, session):
    job = _make_job(session)
    session.commit()

    response = client.get(f"/api/jobs/{job.id}/second-ops")

    assert response.status_code == 200
    body = response.json()
    assert body["state"] == "unaudited"
    assert body["lines"] == []


def test_get_on_a_shipped_job_is_200(client, session):
    job = _make_job(session, status=JobStatus.shipped)
    session.commit()

    assert client.get(f"/api/jobs/{job.id}/second-ops").status_code == 200


def test_get_on_a_nonexistent_job_is_404(client):
    assert client.get("/api/jobs/999999/second-ops").status_code == 404


def test_get_limits_reflect_settings_without_a_client_rebuild(client, session):
    job = _make_job(session)
    session.commit()
    _override_settings(client, second_ops_max_lines=17)

    body = client.get(f"/api/jobs/{job.id}/second-ops").json()

    assert body["limits"]["max_lines"] == 17


# ---------------------------------------------------------------------------
# JobReadExpanded.second_ops asymmetry
# ---------------------------------------------------------------------------


def test_shipping_and_history_endpoints_carry_the_summary(client, session):
    planned = _make_job(session, part_number="PLANNED-1")
    shipped = _make_job(session, part_number="SHIPPED-1", status=JobStatus.shipped)
    _add_lines(session, planned, 5)
    _add_lines(session, shipped, 5)
    for job in (planned, shipped):
        job.second_ops_reviewed_at = datetime(2026, 8, 1)
    session.commit()

    shipping_body = client.get("/api/jobs/shipping").json()
    history_body = client.get("/api/jobs/history").json()

    assert shipping_body[0]["second_ops"]["state"] == "recorded"
    assert shipping_body[0]["second_ops"]["line_count"] == 5
    assert len(shipping_body[0]["second_ops"]["preview"]) == 3
    assert history_body[0]["second_ops"]["line_count"] == 5


@pytest.mark.parametrize(
    "path_template",
    ["/api/jobs", "/api/jobs/{job_id}", "/api/jobs/discarded", "/api/jobs/{job_id}/lineage"],
)
def test_other_endpoints_serialize_second_ops_as_null(client, session, path_template):
    """`= None` on the schema field is what keeps these seven producers green."""
    job = _make_job(session, discarded_at=datetime(2026, 8, 20))
    other = _make_job(session, part_number="ACTIVE-1")
    session.commit()

    path = path_template.format(job_id=other.id)
    response = client.get(path)

    assert response.status_code == 200
    body = response.json()
    rows = body if isinstance(body, list) else [body]
    assert rows, "expected at least one row to assert on"
    assert all(row["second_ops"] is None for row in rows)


def test_job_read_expanded_validates_an_orm_instance_without_the_attribute(session):
    job = _make_job(session)
    session.flush()
    session.refresh(job, ["assembly", "customer"])

    assert JobReadExpanded.model_validate(job).second_ops is None


# ---------------------------------------------------------------------------
# Cascade
# ---------------------------------------------------------------------------


def test_deleting_an_assembly_cascades_to_second_ops_lines(session: Session):
    job = _make_job(session, part_number="CASCADE-1")
    _add_lines(session, job, 3)
    session.flush()
    assembly = session.get(Assembly, job.assembly_id)

    session.delete(assembly)
    session.flush()

    assert session.scalars(select(JobSecondOpsLine)).all() == []
