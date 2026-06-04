import json
import logging
import sys
from pathlib import Path
from datetime import datetime, timezone

from backend.app.config import get_settings
from backend.app.db import SessionLocal, init_db
from backend.app.models import Job, Assembly, ImportBatch, ImportStagingRow, JobStatus, SheetKind, ImportStatus
from backend.app.ingest import sweep_missing_planned_jobs

def main():
    logging.basicConfig(level=logging.INFO)
    init_db()
    with SessionLocal() as session:
        session.execute("DELETE FROM import_staging")
        session.execute("DELETE FROM import_batches")
        session.execute("DELETE FROM jobs")
        session.execute("DELETE FROM assemblies")
        session.commit()

        # Create original jobs
        a1 = Assembly(part_number="138537")
        a2 = Assembly(part_number="138538")
        session.add(a1)
        session.add(a2)
        session.flush()

        # Job 1, 2, 3 (Active, Planned)
        j1 = Job(assembly_id=a1.id, build_type="new", quantity=150, status=JobStatus.planned)
        j2 = Job(assembly_id=a2.id, build_type="new", quantity=100, status=JobStatus.planned)
        j3 = Job(assembly_id=a2.id, build_type="new", split_suffix="-1par", quantity=50, status=JobStatus.planned)
        session.add_all([j1, j2, j3])
        session.commit()

        # Simulate batch that resolves only j1 and j3
        batch1 = ImportBatch(source_file="test1.xlsx", source_sha256="abc", row_count=2, sheet_kind=SheetKind.live, status=ImportStatus.processed)
        session.add(batch1)
        session.flush()

        r1 = ImportStagingRow(batch_id=batch1.id, source_row_number=1, resolved_job_id=j1.id)
        r2 = ImportStagingRow(batch_id=batch1.id, source_row_number=2, resolved_job_id=j3.id)
        session.add_all([r1, r2])
        session.commit()

        # Run sweep
        report = sweep_missing_planned_jobs(session, batch1.id, SheetKind.live, ImportStatus.processed)
        session.commit()

        print("Sweep Report:", report)

        jobs = session.query(Job).all()
        for j in jobs:
            print(f"Job {j.id}: discarded_at={j.discarded_at}")

if __name__ == '__main__':
    main()
