"""Patch 06 — intra-file duplicate resolution: one identity, one gate, one source of truth.

Covers Architecture/20260916-Patch06.md §7.2 (identity and section) and §7.3
(mutations).  §7.1 lives in test_ingest_intra_file_duplicates.py and §7.4 in
test_migration_0013.py.
"""
from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import func, select

from backend.app.api.ingest import (
    build_duplicate_section,
    derive_row_review_status,
    find_identity_collisions,
)
from backend.app.extractors import DecomposeError
from backend.app.ingest import ingest_workbook, run_stages_4_to_6
from backend.app.models import (
    BuildType,
    ImportBatch,
    ImportStagingRow,
    ImportStatus,
    Job,
    SheetKind,
)
from backend.app.transform import (
    IdentityTuple,
    Unparseable,
    effective_identity,
    group_by_effective_identity,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_batch(session, *, status: ImportStatus = ImportStatus.awaiting_review) -> ImportBatch:
    batch = ImportBatch(
        source_file="patch06.xlsx",
        source_sha256="06" * 32,
        status=status,
        sheet_kind=SheetKind.live,
        row_count=0,
    )
    session.add(batch)
    session.flush()
    return batch


def _make_row(
    session,
    batch: ImportBatch,
    *,
    raw_job: str | None,
    source_row_number: int,
    review_status: str | None = "verified",
    parsed_part_number: str | None = None,
    **columns,
) -> ImportStagingRow:
    row = ImportStagingRow(
        batch_id=batch.id,
        source_row_number=source_row_number,
        raw_job=raw_job,
        raw_qty=columns.pop("raw_qty", "10"),
        raw_customer=columns.pop("raw_customer", "ACME"),
        review_status=review_status,
        parsed_part_number=parsed_part_number,
        processing_status=ImportStatus.pending,
        **columns,
    )
    session.add(row)
    session.flush()
    return row


def _held_batch(workbook_factory, session_factory, rows: list[dict[str, str]]) -> int:
    held = ingest_workbook(workbook_factory(rows), session_factory=session_factory)
    assert held.kind == "held_for_review"
    return held.batch_id


def _row_ids(session_factory, batch_id: int) -> list[int]:
    with session_factory() as s:
        return list(s.scalars(
            select(ImportStagingRow.id)
            .where(ImportStagingRow.batch_id == batch_id)
            .order_by(ImportStagingRow.source_row_number)
        ).all())


def _patch_suffix(client, batch_id: int, row_id: int, suffix: str | None):
    return client.patch(
        f"/api/ingest/{batch_id}/staging-row/{row_id}/split-suffix",
        json={"split_suffix": suffix},
    )


def _revert_suffix(client, batch_id: int, row_id: int):
    return client.delete(f"/api/ingest/{batch_id}/staging-row/{row_id}/split-suffix")


def _put_canonical(client, batch_id: int, parsed_pn: str, canonical: str):
    return client.put(
        f"/api/ingest/{batch_id}/canonical/{parsed_pn}",
        json={"canonical_part_number": canonical},
    )


def _reload(session_factory, row_id: int) -> ImportStagingRow:
    """Fresh copy of a staging row with its deferred review columns loaded."""
    with session_factory() as s:
        row = s.get(ImportStagingRow, row_id)
        _ = (
            row.review_status, row.reviewed_at, row.original_raw_job,
            row.review_part_number_override, row.review_split_suffix_override,
            row.review_split_suffix_source,
        )
        return row


# ---------------------------------------------------------------------------
# §7.2 — effective_identity
# ---------------------------------------------------------------------------

class TestEffectiveIdentity:
    def test_equals_raw_identity_when_no_overrides(self):
        row = ImportStagingRow(batch_id=1, source_row_number=1, raw_job="137845-1par\nRONC 44")
        assert effective_identity(row) == IdentityTuple(
            part_number="137845",
            build_type=BuildType.ronc,
            split_suffix="-1par",
            repeat_reference="44",
            build_qualifier=None,
        )

    def test_reflects_both_overrides(self):
        row = ImportStagingRow(
            batch_id=1, source_row_number=1, raw_job="137845-1par\nNEW",
            review_part_number_override="999999", review_split_suffix_override="-7par",
        )
        identity = effective_identity(row)
        assert (identity.part_number, identity.split_suffix) == ("999999", "-7par")
        assert identity.build_type == BuildType.new

    def test_ignores_suffix_override_without_part_number_override(self):
        row = ImportStagingRow(
            batch_id=1, source_row_number=1, raw_job="137845-1par\nNEW",
            review_split_suffix_override="-7par",
        )
        assert effective_identity(row).split_suffix == "-1par"

    def test_parse_failure_returned_as_value(self):
        row = ImportStagingRow(batch_id=1, source_row_number=1, raw_job="NOTPARSEABLE")
        assert isinstance(effective_identity(row), DecomposeError)

    @pytest.mark.parametrize("raw_job", [None, ""])
    def test_empty_raw_job_is_unparseable_empty(self, raw_job):
        row = ImportStagingRow(batch_id=1, source_row_number=1, raw_job=raw_job)
        assert effective_identity(row) is Unparseable.EMPTY


class TestGroupByEffectiveIdentity:
    def test_sets_are_ordered_by_lowest_source_row_and_exclude_unparseable(self, session):
        batch = _make_batch(session)
        late_a = _make_row(session, batch, raw_job="222222\nNEW", source_row_number=5)
        early_a = _make_row(session, batch, raw_job="111111\nNEW", source_row_number=2)
        late_b = _make_row(session, batch, raw_job="222222\nNEW", source_row_number=3)
        early_b = _make_row(session, batch, raw_job="111111\nNEW", source_row_number=9)
        _make_row(session, batch, raw_job="NOTPARSEABLE", source_row_number=1)
        _make_row(session, batch, raw_job="NOTPARSEABLE", source_row_number=4)
        _make_row(session, batch, raw_job=None, source_row_number=6)
        _make_row(session, batch, raw_job=None, source_row_number=7)

        collisions = group_by_effective_identity(session.scalars(
            select(ImportStagingRow).where(ImportStagingRow.batch_id == batch.id)
        ).all())

        assert [c.row_ids for c in collisions] == [
            (early_a.id, early_b.id),
            (late_b.id, late_a.id),
        ]


# ---------------------------------------------------------------------------
# §7.2 — find_identity_collisions scope (Audit 1 rationale)
# ---------------------------------------------------------------------------

class TestFindIdentityCollisions:
    def test_excludes_discarded_rows(self, session):
        batch = _make_batch(session)
        _make_row(session, batch, raw_job="137845\nNEW", source_row_number=1)
        _make_row(
            session, batch, raw_job="137845\nNEW", source_row_number=2,
            review_status="deleted", discarded_at=datetime.now(UTC).replace(tzinfo=None),
        )
        session.commit()
        assert find_identity_collisions(session, batch.id) == []

    def test_includes_rows_never_shown_for_review(self, session):
        batch = _make_batch(session)
        renamed = _make_row(
            session, batch, raw_job="137845\nNEW", source_row_number=1,
            review_part_number_override="137845", review_split_suffix_override="-2par",
            review_split_suffix_source="operator", review_status="edited",
        )
        untouched = _make_row(
            session, batch, raw_job="137845-2par\nNEW", source_row_number=2, review_status=None,
        )
        session.commit()
        collisions = find_identity_collisions(session, batch.id)
        assert [c.row_ids for c in collisions] == [(renamed.id, untouched.id)]


# ---------------------------------------------------------------------------
# §7.2 — duplicate section through the API
# ---------------------------------------------------------------------------

DUPLICATE_PAIR = [
    {"JOB": "137845\nNEW", "QTY": "10", "CUSTOMER": "ACME"},
    {"JOB": "137845\nNEW", "QTY": "25", "CUSTOMER": "ACME"},
]


class TestDuplicateSection:
    def test_rename_resolves_group_and_confirm_writes_two_jobs(
        self, client, workbook_factory, session_factory
    ):
        batch_id = _held_batch(workbook_factory, session_factory, DUPLICATE_PAIR)
        first_id, second_id = _row_ids(session_factory, batch_id)

        resp = _patch_suffix(client, batch_id, second_id, "-2par")

        assert resp.status_code == 200
        (group,) = resp.json()["intra_file_duplicates"]
        assert group["origin"] == "staged"
        assert group["resolved"] is True
        renamed = next(r for r in group["rows"] if r["staging_row_id"] == second_id)
        assert renamed["effective_identity"]["split_suffix"] == "-2par"
        assert renamed["review_split_suffix_source"] == "operator"

        confirm = client.post(f"/api/ingest/{batch_id}/confirm")
        assert confirm.status_code == 200, confirm.json()
        assert confirm.json()["rows_inserted"] == 2
        with session_factory() as s:
            jobs = {(j.split_suffix, j.quantity) for j in s.scalars(select(Job)).all()}
        assert jobs == {(None, 10), ("-2par", 25)}

    def test_delete_resolves_group_and_keeps_deleted_row_listed(
        self, client, workbook_factory, session_factory
    ):
        batch_id = _held_batch(workbook_factory, session_factory, DUPLICATE_PAIR)
        first_id, second_id = _row_ids(session_factory, batch_id)

        resp = client.delete(f"/api/ingest/{batch_id}/staging-row/{second_id}")

        assert resp.status_code == 200
        (group,) = resp.json()["intra_file_duplicates"]
        assert group["resolved"] is True
        assert {r["staging_row_id"]: r["review_status"] for r in group["rows"]} == {
            first_id: "verified",
            second_id: "deleted",
        }
        assert client.post(f"/api/ingest/{batch_id}/confirm").status_code == 200

    def test_rename_into_unreviewed_row_surfaces_edit_induced_group(
        self, client, workbook_factory, session_factory
    ):
        batch_id = _held_batch(workbook_factory, session_factory, DUPLICATE_PAIR + [
            {"JOB": "137845-2par\nNEW", "QTY": "3", "CUSTOMER": "ACME"},
        ])
        first_id, second_id, bystander_id = _row_ids(session_factory, batch_id)
        with session_factory() as s:
            assert s.get(ImportStagingRow, bystander_id).review_status is None

        resp = _patch_suffix(client, batch_id, first_id, "-2par")

        assert resp.status_code == 200
        staged, edit_induced = resp.json()["intra_file_duplicates"]
        assert (staged["origin"], staged["resolved"]) == ("staged", False)
        assert (edit_induced["origin"], edit_induced["resolved"]) == ("edit_induced", False)
        assert edit_induced["identity"]["split_suffix"] == "-2par"
        actionable = {r["staging_row_id"]: r["actionable"] for r in edit_induced["rows"]}
        assert actionable == {first_id: True, bystander_id: False}

        confirm = client.post(f"/api/ingest/{batch_id}/confirm")
        assert confirm.status_code == 409
        assert confirm.json()["code"] == "identity_collision"
        assert confirm.json()["collisions"][0]["row_ids"] == [first_id, bystander_id]
        with session_factory() as s:
            assert s.scalar(select(func.count()).select_from(Job)) == 0

    def test_partial_rename_inside_staged_group_emits_no_edit_induced_group(
        self, client, workbook_factory, session_factory
    ):
        batch_id = _held_batch(workbook_factory, session_factory, DUPLICATE_PAIR + [
            {"JOB": "137845\nNEW", "QTY": "7", "CUSTOMER": "ACME"},
        ])
        _, _, third_id = _row_ids(session_factory, batch_id)

        resp = _patch_suffix(client, batch_id, third_id, "-3par")

        (group,) = resp.json()["intra_file_duplicates"]
        assert group["origin"] == "staged"
        assert group["resolved"] is False
        assert len(group["rows"]) == 3

    def test_groups_sharing_part_number_report_their_own_status(
        self, client, workbook_factory, session_factory
    ):
        batch_id = _held_batch(workbook_factory, session_factory, DUPLICATE_PAIR + [
            {"JOB": "137845\nRONC", "QTY": "4", "CUSTOMER": "ACME"},
            {"JOB": "137845\nRONC", "QTY": "5", "CUSTOMER": "ACME"},
        ])
        _, _, ronc_id, _ = _row_ids(session_factory, batch_id)

        resp = _patch_suffix(client, batch_id, ronc_id, "-1par")

        assert resp.status_code == 200
        body = resp.json()
        new_group, ronc_group = body["intra_file_duplicates"]
        assert new_group["identity"]["build_type"] == "new"
        assert ronc_group["identity"]["build_type"] == "ronc"
        assert (new_group["review_status"], new_group["resolved"]) == ("verified", False)
        assert (ronc_group["review_status"], ronc_group["resolved"]) == ("edited", True)
        assert body["group"]["review_status"] == "edited"

    def test_pending_rows_are_actionable(self, client, session):
        batch = _make_batch(session)
        pending = _make_row(session, batch, raw_job="137845\nNEW", source_row_number=1, review_status="pending")
        _make_row(session, batch, raw_job="137845\nNEW", source_row_number=2, review_status="pending")
        session.commit()

        section = build_duplicate_section(session, batch.id)

        (group,) = section
        assert all(r["actionable"] for r in group["rows"])
        assert client.post(f"/api/ingest/{batch.id}/staging-row/{pending.id}/verify").status_code == 200


# ---------------------------------------------------------------------------
# §2.1 — shared review-status rule
# ---------------------------------------------------------------------------

class TestDeriveRowReviewStatus:
    @pytest.mark.parametrize(
        ("pn_override", "suffix_override", "expected"),
        [
            (None, None, "verified"),
            ("137845", "-1par", "verified"),
            ("137845", None, "edited"),
            ("137845", "-2par", "edited"),
            ("654321", "-1par", "edited"),
            (None, "-2par", "verified"),
        ],
    )
    def test_status_follows_effective_pair(self, pn_override, suffix_override, expected):
        row = ImportStagingRow(
            batch_id=1, source_row_number=1, raw_job="137845-1par\nNEW",
            review_part_number_override=pn_override,
            review_split_suffix_override=suffix_override,
        )
        assert derive_row_review_status(row) == expected

    def test_override_on_unparseable_row_is_edited(self):
        row = ImportStagingRow(
            batch_id=1, source_row_number=1, raw_job="NOTPARSEABLE",
            review_part_number_override="NOTPARSEABLE",
        )
        assert derive_row_review_status(row) == "edited"


# ---------------------------------------------------------------------------
# §7.3 — PATCH /split-suffix
# ---------------------------------------------------------------------------

class TestPatchSplitSuffix:
    def test_seeds_part_number_override_from_parse(self, client, session, session_factory):
        batch = _make_batch(session)
        row = _make_row(session, batch, raw_job="137845\nNEW", source_row_number=1, parsed_part_number="137845")
        session.commit()

        resp = _patch_suffix(client, batch.id, row.id, "-2par")

        assert resp.status_code == 200
        assert resp.json()["row"]["review_split_suffix_source"] == "operator"
        stored = _reload(session_factory, row.id)
        assert stored.review_part_number_override == "137845"
        assert stored.review_split_suffix_override == "-2par"
        assert stored.review_split_suffix_source == "operator"
        assert stored.review_status == "edited"

    def test_suffix_equal_to_parse_is_verified(self, client, session, session_factory):
        batch = _make_batch(session)
        row = _make_row(session, batch, raw_job="137845-1par\nNEW", source_row_number=1)
        session.commit()

        assert _patch_suffix(client, batch.id, row.id, "-1par").status_code == 200

        assert _reload(session_factory, row.id).review_status == "verified"

    def test_null_suffix_means_explicitly_none(self, client, session, session_factory):
        batch = _make_batch(session)
        row = _make_row(session, batch, raw_job="137845-1par\nNEW", source_row_number=1)
        session.commit()

        assert _patch_suffix(client, batch.id, row.id, None).status_code == 200

        stored = _reload(session_factory, row.id)
        assert effective_identity(stored).split_suffix is None
        assert stored.review_split_suffix_source == "operator"
        assert stored.review_status == "edited"

    def test_row_without_parsed_part_number_returns_422_without_write(
        self, client, session, session_factory
    ):
        batch = _make_batch(session)
        row = _make_row(session, batch, raw_job="NOTPARSEABLE", source_row_number=1)
        session.commit()

        resp = _patch_suffix(client, batch.id, row.id, "-2par")

        assert resp.status_code == 422
        stored = _reload(session_factory, row.id)
        assert stored.review_part_number_override is None
        assert stored.review_split_suffix_override is None
        assert stored.review_split_suffix_source is None
        assert stored.reviewed_at is None


# ---------------------------------------------------------------------------
# §7.3 — PUT /canonical
# ---------------------------------------------------------------------------

class TestSetCanonicalProvenance:
    def test_preserves_operator_suffix_and_replaces_part_number(self, client, session, session_factory):
        batch = _make_batch(session)
        row = _make_row(session, batch, raw_job="137845\nNEW", source_row_number=1, parsed_part_number="137845")
        session.commit()
        assert _patch_suffix(client, batch.id, row.id, "-2par").status_code == 200

        resp = _put_canonical(client, batch.id, "137845", "137999")

        assert resp.status_code == 200
        stored = _reload(session_factory, row.id)
        assert stored.review_part_number_override == "137999"
        assert stored.review_split_suffix_override == "-2par"
        assert stored.review_split_suffix_source == "operator"
        assert stored.review_status == "edited"

    def test_recomputes_computed_suffix_and_marks_source(self, client, session, session_factory):
        batch = _make_batch(session)
        row = _make_row(session, batch, raw_job="137845-1par\nNEW", source_row_number=1, parsed_part_number="137845")
        session.commit()

        assert _put_canonical(client, batch.id, "137845", "137845").status_code == 200

        stored = _reload(session_factory, row.id)
        assert stored.review_split_suffix_override == "-1par"
        assert stored.review_split_suffix_source == "computed"
        assert stored.review_status == "verified"

    def test_overlong_computed_suffix_returns_422_and_mutates_no_row(
        self, client, session, session_factory
    ):
        batch = _make_batch(session)
        short = _make_row(session, batch, raw_job="137845\nNEW", source_row_number=1, parsed_part_number="137845")
        long = _make_row(
            session, batch, raw_job="137845-" + "a" * 33 + "\nNEW", source_row_number=2,
            parsed_part_number="137845",
        )
        session.commit()

        resp = _put_canonical(client, batch.id, "137845", "137845")

        assert resp.status_code == 422
        for row_id in (short.id, long.id):
            stored = _reload(session_factory, row_id)
            assert stored.review_part_number_override is None
            assert stored.review_split_suffix_source is None
            assert stored.original_raw_job is None
            assert stored.reviewed_at is None

    def test_operator_suffix_equal_to_parse_survives_canonical_revert(
        self, client, session, session_factory
    ):
        batch = _make_batch(session)
        row = _make_row(session, batch, raw_job="137845-1par\nNEW", source_row_number=1, parsed_part_number="137845")
        session.commit()
        assert _patch_suffix(client, batch.id, row.id, "-1par").status_code == 200

        assert _put_canonical(client, batch.id, "137845", "137845").status_code == 200

        stored = _reload(session_factory, row.id)
        assert stored.review_split_suffix_override == "-1par"
        assert stored.review_split_suffix_source == "operator"
        assert stored.review_status == "verified"

    def test_canonical_change_no_longer_erases_a_resolving_rename(
        self, client, workbook_factory, session_factory
    ):
        """§0.6: rename a duplicate, then re-apply the canonical — the rename survives."""
        batch_id = _held_batch(workbook_factory, session_factory, DUPLICATE_PAIR)
        _, second_id = _row_ids(session_factory, batch_id)
        assert _patch_suffix(client, batch_id, second_id, "-2par").status_code == 200

        resp = _put_canonical(client, batch_id, "137845", "137845")

        assert resp.status_code == 200
        assert [g["resolved"] for g in resp.json()["intra_file_duplicates"]] == [True]
        assert client.post(f"/api/ingest/{batch_id}/confirm").status_code == 200


# ---------------------------------------------------------------------------
# §7.3 — DELETE /split-suffix
# ---------------------------------------------------------------------------

class TestRevertSplitSuffix:
    def test_returns_row_to_computed_and_second_call_is_noop(self, client, session, session_factory):
        batch = _make_batch(session)
        row = _make_row(session, batch, raw_job="137845-1par\nNEW", source_row_number=1, parsed_part_number="137845")
        session.commit()
        assert _patch_suffix(client, batch.id, row.id, "-9par").status_code == 200

        first = _revert_suffix(client, batch.id, row.id)

        assert first.status_code == 200
        assert first.json()["row"]["review_split_suffix_source"] == "computed"
        stored = _reload(session_factory, row.id)
        assert stored.review_split_suffix_override == "-1par"
        assert stored.review_split_suffix_source == "computed"
        # Pair equals the parse: overrides are retained, status reads verified.
        assert stored.review_part_number_override == "137845"
        assert stored.review_status == "verified"
        reviewed_at = stored.reviewed_at

        second = _revert_suffix(client, batch.id, row.id)

        assert second.status_code == 200
        assert second.json()["row"] == first.json()["row"]
        assert _reload(session_factory, row.id).reviewed_at == reviewed_at

    def test_overlong_recomputed_suffix_returns_422_and_leaves_row(
        self, client, session, session_factory
    ):
        batch = _make_batch(session)
        row = _make_row(
            session, batch, raw_job="137845-" + "a" * 30 + "\nNEW", source_row_number=1,
            parsed_part_number="137845",
        )
        session.commit()
        assert _patch_suffix(client, batch.id, row.id, "-1par").status_code == 200
        assert _put_canonical(client, batch.id, "137845", "1378").status_code == 200

        resp = _revert_suffix(client, batch.id, row.id)

        assert resp.status_code == 422
        assert "33" in resp.json()["detail"]
        stored = _reload(session_factory, row.id)
        assert stored.review_part_number_override == "1378"
        assert stored.review_split_suffix_override == "-1par"
        assert stored.review_split_suffix_source == "operator"

    def test_pending_row_is_noop(self, client, session, session_factory):
        batch = _make_batch(session)
        row = _make_row(session, batch, raw_job="137845\nNEW", source_row_number=1, review_status="pending")
        session.commit()

        resp = _revert_suffix(client, batch.id, row.id)

        assert resp.status_code == 200
        stored = _reload(session_factory, row.id)
        assert stored.review_status == "pending"
        assert stored.reviewed_at is None
        assert stored.review_split_suffix_source is None

    def test_deleted_row_returns_409(self, client, session):
        batch = _make_batch(session)
        row = _make_row(session, batch, raw_job="137845\nNEW", source_row_number=1, review_status="deleted")
        session.commit()
        assert _revert_suffix(client, batch.id, row.id).status_code == 409

    def test_row_from_other_batch_returns_404(self, client, session):
        batch = _make_batch(session)
        other = _make_batch(session)
        row = _make_row(session, other, raw_job="137845\nNEW", source_row_number=1)
        session.commit()
        assert _revert_suffix(client, batch.id, row.id).status_code == 404

    def test_batch_not_awaiting_review_returns_409(self, client, session):
        batch = _make_batch(session, status=ImportStatus.processed)
        row = _make_row(session, batch, raw_job="137845\nNEW", source_row_number=1)
        session.commit()
        assert _revert_suffix(client, batch.id, row.id).status_code == 409


# ---------------------------------------------------------------------------
# §7.3 — Stage 4 counters
# ---------------------------------------------------------------------------

def test_stage_4_counts_collisions_and_decompose_errors_once(workbook_factory, session_factory):
    batch_id = _held_batch(workbook_factory, session_factory, DUPLICATE_PAIR + [
        {"JOB": "NOTPARSEABLE", "QTY": "1", "CUSTOMER": "ACME"},
        {"JOB": "137846\nNEW", "QTY": "2", "CUSTOMER": "ACME"},
    ])

    result = run_stages_4_to_6(
        batch_id=batch_id,
        rows_total=4,
        sheet_kind=SheetKind.live,
        source_sha256="",
        filename=None,
        duplicate_of=None,
        session_factory=session_factory,
    )

    assert result.rows_errored == 3
    assert result.rows_inserted == 1


# ---------------------------------------------------------------------------
# §7.3 — every mutation response carries the section GET /review would return
# ---------------------------------------------------------------------------

def test_every_mutation_response_section_matches_get_review(
    client, workbook_factory, session_factory
):
    batch_id = _held_batch(workbook_factory, session_factory, DUPLICATE_PAIR + [
        {"JOB": "137845-2par\nNEW", "QTY": "3", "CUSTOMER": "ACME"},
        {"JOB": "555001\nNEW", "QTY": "1", "CUSTOMER": "ACME"},
    ])
    first_id, second_id, _, _ = _row_ids(session_factory, batch_id)

    def assert_matches(resp):
        assert resp.status_code == 200, resp.json()
        review = client.get(f"/api/ingest/{batch_id}/review")
        assert resp.json()["intra_file_duplicates"] == review.json()["intra_file_duplicates"]

    assert_matches(_patch_suffix(client, batch_id, first_id, "-2par"))
    assert_matches(_put_canonical(client, batch_id, "137845", "137845"))
    assert_matches(_revert_suffix(client, batch_id, first_id))
    assert_matches(client.delete(f"/api/ingest/{batch_id}/staging-row/{second_id}"))

    with session_factory() as s:
        pending = s.get(ImportStagingRow, first_id)
        pending.review_status = "pending"
        s.commit()
    assert_matches(client.post(f"/api/ingest/{batch_id}/staging-row/{first_id}/verify"))
