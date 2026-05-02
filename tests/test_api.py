"""Contract tests for Phase 04 routers."""
from __future__ import annotations

from datetime import date

import pytest
from fastapi import status

from backend.app.models import BuildType, ImportStagingRow, JobStatus


# ---- /api/staging ------------------------------------------------------------


class TestStagingErroredList:
    def test_returns_only_errored_rows(self, client, seeded_mixed_staging):
        resp = client.get("/api/staging/errored")
        assert resp.status_code == status.HTTP_200_OK
        body = resp.json()
        assert all(row["processing_status"] == "error" for row in body)

    def test_x_total_count_header(self, client, seeded_mixed_staging):
        resp = client.get("/api/staging/errored")
        assert "x-total-count" in {k.lower() for k in resp.headers.keys()}
        assert int(resp.headers["x-total-count"]) == seeded_mixed_staging["errored_count"]

    def test_pagination_respects_limit_offset(self, client, seeded_25_errored):
        resp = client.get("/api/staging/errored?limit=10&offset=5")
        assert resp.status_code == status.HTTP_200_OK
        assert len(resp.json()) == 10

    def test_limit_over_max_is_422(self, client):
        resp = client.get("/api/staging/errored?limit=10000")
        assert resp.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY


class TestStagingRowDetail:
    def test_returns_full_raw_payload(self, client, seeded_errored_row):
        resp = client.get(f"/api/staging/{seeded_errored_row.id}")
        assert resp.status_code == status.HTTP_200_OK
        body = resp.json()
        assert body["id"] == seeded_errored_row.id
        # All 21 raw_* fields present (even if null)
        for attr in [
            "raw_shipped", "raw_pcb_notes", "raw_kit_notes", "raw_scheduling_notes",
            "raw_line_1", "raw_line_2", "raw_line_3", "raw_job", "raw_qty",
            "raw_ship_date", "raw_prog", "raw_mfg_notes", "raw_smt_lines",
            "raw_smt_plcmnts", "raw_ship_method", "raw_customer", "raw_sales_p",
            "raw_doc_rel", "raw_kit_rel", "raw_code", "raw_bom_compare_photos",
        ]:
            assert attr in body

    def test_missing_row_is_404(self, client):
        resp = client.get("/api/staging/9999999")
        assert resp.status_code == status.HTTP_404_NOT_FOUND


class TestStagingCorrection:
    def test_successful_correction_returns_job_and_marks_row_processed(
        self, client, session, seeded_errored_row_invalid_job,
    ):
        resp = client.post(
            f"/api/staging/{seeded_errored_row_invalid_job.id}/correct",
            json={"raw_job": "137845\nNEW", "raw_qty": "5", "raw_customer": "ACME"},
        )
        assert resp.status_code == status.HTTP_200_OK
        body = resp.json()
        assert body["quantity"] == 5
        assert body["assembly"]["part_number"] == "137845"
        # Verify staging row state was promoted to processed
        session.expire_all()
        row = session.get(type(seeded_errored_row_invalid_job), seeded_errored_row_invalid_job.id)
        assert row.processing_status.value == "processed"
        assert row.resolved_job_id == body["id"]

    def test_transform_still_fails_returns_422_with_new_error(
        self, client, session, seeded_errored_row_invalid_job,
    ):
        resp = client.post(
            f"/api/staging/{seeded_errored_row_invalid_job.id}/correct",
            json={"raw_job": "still nonsense"},
        )
        assert resp.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY
        assert "Invalid JOB cell" in resp.json()["detail"]
        session.expire_all()
        row = session.get(type(seeded_errored_row_invalid_job), seeded_errored_row_invalid_job.id)
        assert row.processing_status.value == "error"

    def test_correct_on_already_processed_row_is_409(
        self, client, seeded_processed_row,
    ):
        resp = client.post(
            f"/api/staging/{seeded_processed_row.id}/correct",
            json={"raw_qty": "99"},
        )
        assert resp.status_code == status.HTTP_409_CONFLICT

    def test_correct_unknown_row_is_404(self, client):
        resp = client.post(
            "/api/staging/9999999/correct",
            json={"raw_qty": "1"},
        )
        assert resp.status_code == status.HTTP_404_NOT_FOUND

    def test_unknown_field_in_payload_rejected(self, client, seeded_errored_row):
        resp = client.post(
            f"/api/staging/{seeded_errored_row.id}/correct",
            json={"not_a_raw_field": "anything"},
        )
        assert resp.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY

    def test_explicit_null_clears_the_cell(self, client, session, seeded_errored_row):
        resp = client.post(
            f"/api/staging/{seeded_errored_row.id}/correct",
            json={"raw_prog": None, "raw_job": "137845\nNEW", "raw_qty": "1", "raw_customer": "X"},
        )
        # Subsequent GET should show raw_prog explicitly null, not unchanged
        detail = client.get(f"/api/staging/{seeded_errored_row.id}").json()
        assert detail["raw_prog"] is None

    def test_correct_clears_suggested_correction(
        self, client, session, seeded_errored_row_invalid_job,
    ):
        row = seeded_errored_row_invalid_job
        row.suggested_correction = "Some suggestion"
        session.add(row)
        session.commit()

        resp = client.post(
            f"/api/staging/{row.id}/correct",
            json={"raw_job": "137845\nNEW", "raw_qty": "5", "raw_customer": "ACME"},
        )
        assert resp.status_code == status.HTTP_200_OK
        session.expire_all()
        updated = session.get(type(row), row.id)
        assert updated.suggested_correction is None


# ---- /api/jobs ---------------------------------------------------------------


class TestJobsList:
    def test_returns_flat_list_with_expanded_assembly_and_customer(
        self, client, seeded_jobs,
    ):
        resp = client.get("/api/jobs")
        assert resp.status_code == status.HTTP_200_OK
        body = resp.json()
        assert len(body) >= 1
        first = body[0]
        assert "assembly" in first and "part_number" in first["assembly"]
        assert "customer" in first and "name" in first["customer"]

    def test_status_filter_accepts_csv(self, client, seeded_mixed_status_jobs):
        resp = client.get("/api/jobs?status=planned,wip")
        assert resp.status_code == status.HTTP_200_OK
        for job in resp.json():
            assert job["status"] in ("planned", "wip")

    def test_assembly_filter_returns_all_splits(self, client, seeded_split_jobs):
        assembly_id = seeded_split_jobs["assembly_id"]
        resp = client.get(f"/api/jobs?assembly_id={assembly_id}")
        assert resp.status_code == status.HTTP_200_OK
        body = resp.json()
        assert len(body) == 3
        assert {job["split_suffix"] for job in body} == {"-1par", "-2bal", None}

    def test_build_type_filter(self, client, seeded_mixed_build_jobs):
        resp = client.get(f"/api/jobs?build_type={BuildType.ronc.value}")
        assert resp.status_code == status.HTTP_200_OK
        for job in resp.json():
            assert job["build_type"] == "ronc"

    def test_x_total_count_header(self, client, seeded_50_jobs):
        resp = client.get("/api/jobs?limit=10")
        assert int(resp.headers["x-total-count"]) == 50
        assert len(resp.json()) == 10

    def test_default_ordering_is_stable(self, client, seeded_jobs_with_ship_dates):
        resp = client.get("/api/jobs")
        ship_dates = [j["shipped_at"] or "9999-12-31" for j in resp.json()]
        # NULLS LAST, ASC
        assert ship_dates == sorted(ship_dates)

    def test_markdown_notes_pass_through_as_raw(self, client, seeded_job_markdown):
        resp = client.get(f"/api/jobs/{seeded_job_markdown.id}")
        assert "**bold**" in resp.json()["run_mfg_notes"]


class TestJobDetail:
    def test_unknown_job_is_404(self, client):
        resp = client.get("/api/jobs/9999999")
        assert resp.status_code == status.HTTP_404_NOT_FOUND

    def test_expanded_payload_shape(self, client, seeded_job):
        resp = client.get(f"/api/jobs/{seeded_job.id}")
        assert resp.status_code == status.HTTP_200_OK
        body = resp.json()
        assert body["assembly"]["id"] == seeded_job.assembly_id
        assert body["customer"]["id"] == seeded_job.customer_id


# ---- /api/assemblies ---------------------------------------------------------


class TestAssembliesList:
    def test_returns_list(self, client, seeded_assemblies):
        resp = client.get("/api/assemblies")
        assert resp.status_code == status.HTTP_200_OK
        assert len(resp.json()) >= 1


class TestAssemblyJobsDrilldown:
    def test_returns_assembly_plus_all_jobs(self, client, seeded_split_jobs):
        assembly_id = seeded_split_jobs["assembly_id"]
        resp = client.get(f"/api/assemblies/{assembly_id}/jobs")
        assert resp.status_code == status.HTTP_200_OK
        body = resp.json()
        assert body["id"] == assembly_id
        assert "jobs" in body
        assert len(body["jobs"]) == 3

    def test_drilldown_respects_status_filter(self, client, seeded_split_jobs_mixed_status):
        assembly_id = seeded_split_jobs_mixed_status["assembly_id"]
        resp = client.get(f"/api/assemblies/{assembly_id}/jobs?status=planned")
        for job in resp.json()["jobs"]:
            assert job["status"] == "planned"

    def test_unknown_assembly_is_404(self, client):
        resp = client.get("/api/assemblies/9999999/jobs")
        assert resp.status_code == status.HTTP_404_NOT_FOUND


# ---- /api/jobs/shipping ------------------------------------------------------


class TestShippingList:
    def test_list_shipping_excludes_shipped(self, client, seeded_mixed_status_jobs):
        resp = client.get("/api/jobs/shipping")
        assert resp.status_code == status.HTTP_200_OK
        body = resp.json()
        assert len(body) == 2
        for job in body:
            assert job["status"] != "shipped"

    def test_list_shipping_orders_by_resolved_ship_date(self, client, session):
        from datetime import date as dt_date

        from backend.app.models import Assembly, BuildType, Customer, Job

        customer = Customer(name="ShipOrderCo")
        session.add(customer)
        session.flush()

        dates = [dt_date(2026, 5, 1), dt_date(2026, 3, 15), None]
        for i, d in enumerate(dates):
            asm = Assembly(part_number=f"SHIPORD-{i:03d}")
            session.add(asm)
            session.flush()
            session.add(Job(
                assembly_id=asm.id,
                customer_id=customer.id,
                quantity=1,
                build_type=BuildType.new,
                status=JobStatus.planned,
                resolved_ship_date=d,
            ))
        session.commit()

        resp = client.get("/api/jobs/shipping")
        assert resp.status_code == status.HTTP_200_OK
        body = resp.json()
        assert len(body) == 3
        assert body[0]["resolved_ship_date"] == "2026-03-15"
        assert body[1]["resolved_ship_date"] == "2026-05-01"
        assert body[2]["resolved_ship_date"] is None

    def test_list_shipping_expands_assembly_and_customer(self, client, session):
        from backend.app.models import Assembly, BuildType, Customer, Job

        customer = Customer(name="ExpandCo")
        session.add(customer)
        session.flush()
        asm = Assembly(part_number="EXPAND-001", base_mfg_notes="test notes")
        session.add(asm)
        session.flush()
        session.add(Job(
            assembly_id=asm.id,
            customer_id=customer.id,
            quantity=1,
            build_type=BuildType.new,
            status=JobStatus.planned,
        ))
        session.commit()

        resp = client.get("/api/jobs/shipping")
        assert resp.status_code == status.HTTP_200_OK
        body = resp.json()
        assert len(body) >= 1
        job = body[0]
        assert job["assembly"]["part_number"] == "EXPAND-001"
        assert job["assembly"]["base_mfg_notes"] == "test notes"
        assert job["customer"]["name"] == "ExpandCo"


# ---- /api/jobs/history -------------------------------------------------------


class TestJobHistory:
    def test_returns_only_shipped_jobs(self, client, session):
        from backend.app.models import Assembly, BuildType, Customer, Job

        customer = Customer(name="HistCo")
        session.add(customer)
        session.flush()
        for i, st in enumerate([JobStatus.shipped, JobStatus.shipped, JobStatus.planned, JobStatus.wip]):
            asm = Assembly(part_number=f"HIST-{i:03d}")
            session.add(asm)
            session.flush()
            session.add(Job(
                assembly_id=asm.id, customer_id=customer.id, quantity=1,
                build_type=BuildType.new, status=st,
                shipped_at=date(2026, 1, i + 1) if st == JobStatus.shipped else None,
            ))
        session.commit()

        resp = client.get("/api/jobs/history")
        assert resp.status_code == status.HTTP_200_OK
        body = resp.json()
        assert len(body) == 2
        for job in body:
            assert job["status"] == "shipped"

    def test_sorts_by_shipped_at_descending(self, client, session):
        from backend.app.models import Assembly, BuildType, Customer, Job

        customer = Customer(name="SortHistCo")
        session.add(customer)
        session.flush()
        dates = [date(2026, 1, 15), date(2026, 3, 20), date(2026, 2, 10)]
        for i, d in enumerate(dates):
            asm = Assembly(part_number=f"SORTHIST-{i:03d}")
            session.add(asm)
            session.flush()
            session.add(Job(
                assembly_id=asm.id, customer_id=customer.id, quantity=1,
                build_type=BuildType.new, status=JobStatus.shipped, shipped_at=d,
            ))
        session.commit()

        resp = client.get("/api/jobs/history")
        body = resp.json()
        assert body[0]["shipped_at"] == "2026-03-20"
        assert body[1]["shipped_at"] == "2026-02-10"
        assert body[2]["shipped_at"] == "2026-01-15"

    def test_nulls_last_on_shipped_at(self, client, session):
        from backend.app.models import Assembly, BuildType, Customer, Job

        customer = Customer(name="NullHistCo")
        session.add(customer)
        session.flush()
        for i, d in enumerate([date(2026, 3, 1), None]):
            asm = Assembly(part_number=f"NULLHIST-{i:03d}")
            session.add(asm)
            session.flush()
            session.add(Job(
                assembly_id=asm.id, customer_id=customer.id, quantity=1,
                build_type=BuildType.new, status=JobStatus.shipped, shipped_at=d,
            ))
        session.commit()

        resp = client.get("/api/jobs/history")
        body = resp.json()
        assert body[0]["shipped_at"] == "2026-03-01"
        assert body[-1]["shipped_at"] is None

    def test_pagination_limit_and_offset(self, client, session):
        from backend.app.models import Assembly, BuildType, Customer, Job

        customer = Customer(name="PageHistCo")
        session.add(customer)
        session.flush()
        for i in range(5):
            asm = Assembly(part_number=f"PAGEHIST-{i:03d}")
            session.add(asm)
            session.flush()
            session.add(Job(
                assembly_id=asm.id, customer_id=customer.id, quantity=1,
                build_type=BuildType.new, status=JobStatus.shipped,
                shipped_at=date(2026, 1, i + 1),
            ))
        session.commit()

        resp = client.get("/api/jobs/history?limit=2&offset=2")
        assert resp.status_code == status.HTTP_200_OK
        body = resp.json()
        assert len(body) == 2
        assert resp.headers["X-Total-Count"] == "5"

    def test_default_limit_is_50(self, client, session):
        from backend.app.models import Assembly, BuildType, Customer, Job

        customer = Customer(name="Def50Co")
        session.add(customer)
        session.flush()
        for i in range(75):
            asm = Assembly(part_number=f"DEF50-{i:03d}")
            session.add(asm)
            session.flush()
            session.add(Job(
                assembly_id=asm.id, customer_id=customer.id, quantity=1,
                build_type=BuildType.new, status=JobStatus.shipped,
                shipped_at=date(2026, 1, 1),
            ))
        session.commit()

        resp = client.get("/api/jobs/history")
        body = resp.json()
        assert len(body) == 50
        assert resp.headers["X-Total-Count"] == "75"

    def test_expansion_includes_assembly_and_customer(self, client, session):
        from backend.app.models import Assembly, BuildType, Customer, Job

        customer = Customer(name="ExpandHistCo")
        session.add(customer)
        session.flush()
        asm = Assembly(part_number="EXPANDHIST-001")
        session.add(asm)
        session.flush()
        session.add(Job(
            assembly_id=asm.id, customer_id=customer.id, quantity=1,
            build_type=BuildType.new, status=JobStatus.shipped,
            shipped_at=date(2026, 2, 1),
        ))
        session.commit()

        resp = client.get("/api/jobs/history")
        body = resp.json()
        assert body[0]["assembly"]["part_number"] == "EXPANDHIST-001"
        assert body[0]["customer"]["name"] == "ExpandHistCo"

    def test_empty_when_no_shipped_jobs(self, client, session):
        from backend.app.models import Assembly, BuildType, Customer, Job

        customer = Customer(name="NoShipCo")
        session.add(customer)
        session.flush()
        asm = Assembly(part_number="NOSHIP-001")
        session.add(asm)
        session.flush()
        session.add(Job(
            assembly_id=asm.id, customer_id=customer.id, quantity=1,
            build_type=BuildType.new, status=JobStatus.planned,
        ))
        session.commit()

        resp = client.get("/api/jobs/history")
        assert resp.status_code == status.HTTP_200_OK
        body = resp.json()
        assert body == []
        assert resp.headers["X-Total-Count"] == "0"

    def test_search_matches_part_number(self, client, session):
        from backend.app.models import Assembly, BuildType, Customer, Job

        customer = Customer(name="SearchCo")
        session.add(customer)
        session.flush()
        for pn in ["135458", "999000", "135459"]:
            asm = Assembly(part_number=pn)
            session.add(asm)
            session.flush()
            session.add(Job(
                assembly_id=asm.id, customer_id=customer.id, quantity=1,
                build_type=BuildType.new, status=JobStatus.shipped,
                shipped_at=date(2026, 1, 1),
            ))
        session.commit()

        resp = client.get("/api/jobs/history?search=135458")
        assert resp.status_code == status.HTTP_200_OK
        body = resp.json()
        assert len(body) == 1
        assert body[0]["assembly"]["part_number"] == "135458"
        assert resp.headers["X-Total-Count"] == "1"

    def test_search_matches_customer_name_case_insensitive(self, client, session):
        from backend.app.models import Assembly, BuildType, Customer, Job

        acme = Customer(name="Acme Corp")
        other = Customer(name="OtherCo")
        session.add_all([acme, other])
        session.flush()
        for cust in [acme, other]:
            asm = Assembly(part_number=f"CUST-{cust.id}")
            session.add(asm)
            session.flush()
            session.add(Job(
                assembly_id=asm.id, customer_id=cust.id, quantity=1,
                build_type=BuildType.new, status=JobStatus.shipped,
                shipped_at=date(2026, 1, 1),
            ))
        session.commit()

        resp = client.get("/api/jobs/history?search=ACME")
        body = resp.json()
        assert len(body) == 1
        assert body[0]["customer"]["name"] == "Acme Corp"

    def test_search_matches_base_mfg_notes(self, client, session):
        from backend.app.models import Assembly, BuildType, Customer, Job

        customer = Customer(name="NotesCo")
        session.add(customer)
        session.flush()
        a = Assembly(part_number="NOTES-A", base_mfg_notes="Requires ENIG finish")
        b = Assembly(part_number="NOTES-B", base_mfg_notes="HASL only")
        session.add_all([a, b])
        session.flush()
        for asm in [a, b]:
            session.add(Job(
                assembly_id=asm.id, customer_id=customer.id, quantity=1,
                build_type=BuildType.new, status=JobStatus.shipped,
                shipped_at=date(2026, 1, 1),
            ))
        session.commit()

        resp = client.get("/api/jobs/history?search=ENIG")
        body = resp.json()
        assert len(body) == 1
        assert body[0]["assembly"]["part_number"] == "NOTES-A"

    def test_search_empty_string_behaves_as_no_filter(self, client, session):
        from backend.app.models import Assembly, BuildType, Customer, Job

        customer = Customer(name="EmptySearchCo")
        session.add(customer)
        session.flush()
        for i in range(3):
            asm = Assembly(part_number=f"EMPTYSRCH-{i:03d}")
            session.add(asm)
            session.flush()
            session.add(Job(
                assembly_id=asm.id, customer_id=customer.id, quantity=1,
                build_type=BuildType.new, status=JobStatus.shipped,
                shipped_at=date(2026, 1, i + 1),
            ))
        session.commit()

        resp_none = client.get("/api/jobs/history")
        resp_empty = client.get("/api/jobs/history?search=")
        resp_blank = client.get("/api/jobs/history?search=%20%20")
        assert resp_none.json() == resp_empty.json() == resp_blank.json()
        assert resp_empty.headers["X-Total-Count"] == "3"


# ---- /api/jobs/{job_id}/lineage ----------------------------------------------


class TestJobLineage:
    def test_returns_all_jobs_sharing_assembly_id(self, client, session):
        from backend.app.models import Assembly, BuildType, Customer, Job

        customer = Customer(name="LinCo")
        session.add(customer)
        session.flush()
        asm = Assembly(part_number="LIN-001")
        session.add(asm)
        session.flush()
        other_asm = Assembly(part_number="OTHER-001")
        session.add(other_asm)
        session.flush()
        jobs = []
        for sfx in [None, "-1par", "-2bal"]:
            j = Job(assembly_id=asm.id, customer_id=customer.id, quantity=5,
                    build_type=BuildType.new, split_suffix=sfx)
            session.add(j)
            jobs.append(j)
        session.add(Job(assembly_id=other_asm.id, customer_id=customer.id,
                        quantity=1, build_type=BuildType.new))
        session.commit()

        resp = client.get(f"/api/jobs/{jobs[0].id}/lineage")
        assert resp.status_code == status.HTTP_200_OK
        body = resp.json()
        assert len(body) == 3

    def test_includes_the_queried_job_itself(self, client, session):
        from backend.app.models import Assembly, BuildType, Customer, Job

        customer = Customer(name="SelfLinCo")
        session.add(customer)
        session.flush()
        asm = Assembly(part_number="SELFLIN-001")
        session.add(asm)
        session.flush()
        j = Job(assembly_id=asm.id, customer_id=customer.id, quantity=1,
                build_type=BuildType.new)
        session.add(j)
        session.commit()

        resp = client.get(f"/api/jobs/{j.id}/lineage")
        body = resp.json()
        ids = [row["id"] for row in body]
        assert j.id in ids

    def test_spans_all_customers(self, client, session):
        from backend.app.models import Assembly, BuildType, Customer, Job

        c1 = Customer(name="CustA")
        c2 = Customer(name="CustB")
        session.add_all([c1, c2])
        session.flush()
        asm = Assembly(part_number="MULTICUST-001")
        session.add(asm)
        session.flush()
        j1 = Job(assembly_id=asm.id, customer_id=c1.id, quantity=1, build_type=BuildType.new)
        j2 = Job(assembly_id=asm.id, customer_id=c1.id, quantity=1, build_type=BuildType.ronc)
        j3 = Job(assembly_id=asm.id, customer_id=c2.id, quantity=1, build_type=BuildType.rowc)
        session.add_all([j1, j2, j3])
        session.commit()

        resp = client.get(f"/api/jobs/{j1.id}/lineage")
        body = resp.json()
        assert len(body) == 3
        cust_names = {row["customer"]["name"] for row in body}
        assert cust_names == {"CustA", "CustB"}

    def test_includes_all_statuses(self, client, session):
        from backend.app.models import Assembly, BuildType, Customer, Job

        customer = Customer(name="AllStatCo")
        session.add(customer)
        session.flush()
        asm = Assembly(part_number="ALLSTAT-001")
        session.add(asm)
        session.flush()
        jobs = []
        for st in [JobStatus.planned, JobStatus.wip, JobStatus.shipped]:
            j = Job(assembly_id=asm.id, customer_id=customer.id, quantity=1,
                    build_type=BuildType.new, status=st,
                    shipped_at=date(2026, 1, 1) if st == JobStatus.shipped else None)
            session.add(j)
            jobs.append(j)
        session.commit()

        resp = client.get(f"/api/jobs/{jobs[0].id}/lineage")
        body = resp.json()
        statuses = {row["status"] for row in body}
        assert statuses == {"planned", "wip", "shipped"}

    def test_chronological_order_uses_coalesce(self, client, session):
        from backend.app.models import Assembly, BuildType, Customer, Job

        customer = Customer(name="ChronCo")
        session.add(customer)
        session.flush()
        asm = Assembly(part_number="CHRON-001")
        session.add(asm)
        session.flush()
        j_a = Job(assembly_id=asm.id, customer_id=customer.id, quantity=1,
                  build_type=BuildType.new, status=JobStatus.shipped,
                  shipped_at=date(2025, 1, 1), resolved_ship_date=date(2025, 6, 1))
        j_b = Job(assembly_id=asm.id, customer_id=customer.id, quantity=1,
                  build_type=BuildType.ronc, status=JobStatus.planned,
                  resolved_ship_date=date(2025, 3, 1))
        j_c = Job(assembly_id=asm.id, customer_id=customer.id, quantity=1,
                  build_type=BuildType.rowc, status=JobStatus.planned)
        session.add_all([j_a, j_b, j_c])
        session.commit()

        resp = client.get(f"/api/jobs/{j_a.id}/lineage")
        body = resp.json()
        ids = [row["id"] for row in body]
        assert ids == [j_a.id, j_b.id, j_c.id]

    def test_stable_tiebreak_by_id(self, client, session):
        from backend.app.models import Assembly, BuildType, Customer, Job

        customer = Customer(name="TieCo")
        session.add(customer)
        session.flush()
        asm = Assembly(part_number="TIE-001")
        session.add(asm)
        session.flush()
        j1 = Job(assembly_id=asm.id, customer_id=customer.id, quantity=1,
                 build_type=BuildType.new, status=JobStatus.shipped,
                 shipped_at=date(2026, 1, 1))
        j2 = Job(assembly_id=asm.id, customer_id=customer.id, quantity=1,
                 build_type=BuildType.ronc, status=JobStatus.shipped,
                 shipped_at=date(2026, 1, 1))
        session.add_all([j1, j2])
        session.commit()

        resp = client.get(f"/api/jobs/{j1.id}/lineage")
        body = resp.json()
        ids = [row["id"] for row in body]
        assert ids == [j1.id, j2.id]

    def test_returns_404_for_unknown_job(self, client):
        resp = client.get("/api/jobs/99999/lineage")
        assert resp.status_code == status.HTTP_404_NOT_FOUND

    def test_lineage_of_lone_new_job_returns_just_itself(self, client, session):
        from backend.app.models import Assembly, BuildType, Customer, Job

        customer = Customer(name="LoneCo")
        session.add(customer)
        session.flush()
        asm = Assembly(part_number="LONE-001")
        session.add(asm)
        session.flush()
        j = Job(assembly_id=asm.id, customer_id=customer.id, quantity=1,
                build_type=BuildType.new, status=JobStatus.planned)
        session.add(j)
        session.commit()

        resp = client.get(f"/api/jobs/{j.id}/lineage")
        body = resp.json()
        assert len(body) == 1
        assert body[0]["id"] == j.id

    def test_lineage_includes_inbound_repeat_reference_jobs(self, client, session):
        from backend.app.models import Assembly, BuildType, Customer, Job

        customer = Customer(name="InboundRepCo")
        session.add(customer)
        session.flush()
        anchor_asm = Assembly(part_number="ANCHOR-001")
        repeat_asm = Assembly(part_number="REPEATER-001")
        unrelated_asm = Assembly(part_number="UNRELATED-001")
        session.add_all([anchor_asm, repeat_asm, unrelated_asm])
        session.flush()

        anchor = Job(
            assembly_id=anchor_asm.id, customer_id=customer.id, quantity=1,
            build_type=BuildType.new, status=JobStatus.shipped,
            shipped_at=date(2026, 1, 1),
        )
        repeater = Job(
            assembly_id=repeat_asm.id, customer_id=customer.id, quantity=1,
            build_type=BuildType.ronc, status=JobStatus.shipped,
            shipped_at=date(2026, 2, 1), repeat_reference="ANCHOR-001",
        )
        unrelated = Job(
            assembly_id=unrelated_asm.id, customer_id=customer.id, quantity=1,
            build_type=BuildType.new, status=JobStatus.shipped,
            shipped_at=date(2026, 3, 1),
        )
        session.add_all([anchor, repeater, unrelated])
        session.commit()

        resp = client.get(f"/api/jobs/{anchor.id}/lineage")
        body = resp.json()
        ids = {row["id"] for row in body}
        assert ids == {anchor.id, repeater.id}

    def test_lineage_follows_anchor_outbound_repeat_reference(self, client, session):
        from backend.app.models import Assembly, BuildType, Customer, Job

        customer = Customer(name="OutboundRepCo")
        session.add(customer)
        session.flush()
        anchor_asm = Assembly(part_number="NEW-A")
        origin_asm = Assembly(part_number="ORIGIN-Z")
        session.add_all([anchor_asm, origin_asm])
        session.flush()

        anchor = Job(
            assembly_id=anchor_asm.id, customer_id=customer.id, quantity=1,
            build_type=BuildType.ronc, status=JobStatus.shipped,
            shipped_at=date(2026, 3, 1), repeat_reference="ORIGIN-Z",
        )
        origin = Job(
            assembly_id=origin_asm.id, customer_id=customer.id, quantity=1,
            build_type=BuildType.new, status=JobStatus.shipped,
            shipped_at=date(2026, 1, 1),
        )
        session.add_all([anchor, origin])
        session.commit()

        resp = client.get(f"/api/jobs/{anchor.id}/lineage")
        body = resp.json()
        ids = {row["id"] for row in body}
        assert ids == {anchor.id, origin.id}

    def test_lineage_deduplicates_when_both_assembly_and_repeat_match(self, client, session):
        from backend.app.models import Assembly, BuildType, Customer, Job

        customer = Customer(name="DedupCo")
        session.add(customer)
        session.flush()
        asm = Assembly(part_number="DUP-001")
        session.add(asm)
        session.flush()

        anchor = Job(
            assembly_id=asm.id, customer_id=customer.id, quantity=1,
            build_type=BuildType.new, status=JobStatus.shipped,
            shipped_at=date(2026, 1, 1),
        )
        # Pathological: same part_number AND repeat_reference points at the anchor's part.
        both = Job(
            assembly_id=asm.id, customer_id=customer.id, quantity=1,
            build_type=BuildType.ronc, status=JobStatus.shipped,
            shipped_at=date(2026, 2, 1), repeat_reference="DUP-001",
        )
        session.add_all([anchor, both])
        session.commit()

        resp = client.get(f"/api/jobs/{anchor.id}/lineage")
        body = resp.json()
        ids = [row["id"] for row in body]
        # Each id exactly once (no duplication from the OR clause).
        assert sorted(ids) == sorted({anchor.id, both.id})
        assert len(ids) == 2


# ---- route ordering guard (Review A3) ----------------------------------------


def test_history_route_not_shadowed_by_job_id(client):
    resp = client.get("/api/jobs/history")
    assert resp.status_code != 422
