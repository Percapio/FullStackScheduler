from __future__ import annotations

import enum
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    Column,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Table,
    Text,
    func,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, deferred, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class JobStatus(str, enum.Enum):
    planned = "planned"
    wip = "wip"
    shipped = "shipped"


class BuildType(str, enum.Enum):
    new = "new"
    ronc = "ronc"
    rowc = "rowc"


class BuildQualifier(str, enum.Enum):
    """Suffix-grade work-class qualifier (TDD §3.1.1).

    Pre:  raw cell line matches /^(RWK|REWORK|RMA)\b/i.
    Post: cardinality is 3, bounded by business intent.
    Note: independent dimension from split_suffix; co-exists with BuildType.
    """
    rwk = "rwk"
    rework = "rework"
    rma = "rma"


class ImportStatus(str, enum.Enum):
    pending = "pending"
    processed = "processed"
    error = "error"
    awaiting_review = "awaiting_review"
    abandoned = "abandoned"


class SheetKind(str, enum.Enum):
    live = "live"           # SCHD workbook
    historical = "historical"  # SHIPPED (AA) workbook


class CandidateReason(str, enum.Enum):
    orphan_after_split = "orphan_after_split"
    orphan_after_recombine = "orphan_after_recombine"
    orphan_other = "orphan_other"


class CandidateResolution(str, enum.Enum):
    approve = "approve"
    reject = "reject"
    auto_returned = "auto_returned"


assembly_classifications = Table(
    "assembly_classifications",
    Base.metadata,
    Column("assembly_id", ForeignKey("assemblies.id", ondelete="CASCADE"), primary_key=True),
    Column("classification_id", ForeignKey("classifications.id", ondelete="RESTRICT"), primary_key=True),
)




class Customer(Base, TimestampMixin):
    __tablename__ = "customers"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)

    jobs: Mapped[list[Job]] = relationship(back_populates="customer")


class Salesperson(Base, TimestampMixin):
    __tablename__ = "salespeople"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(16), unique=True, nullable=False)
    name: Mapped[str | None] = mapped_column(String(128))

    jobs: Mapped[list[Job]] = relationship(back_populates="salesperson")


class Classification(Base, TimestampMixin):
    __tablename__ = "classifications"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(32), unique=True, nullable=False)
    description: Mapped[str | None] = mapped_column(String(255))

    assemblies: Mapped[list[Assembly]] = relationship(
        secondary=assembly_classifications, back_populates="classifications"
    )


class Assembly(Base, TimestampMixin):
    __tablename__ = "assemblies"

    id: Mapped[int] = mapped_column(primary_key=True)
    part_number: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)

    program_name: Mapped[str | None] = mapped_column(String(64))
    smt_placements: Mapped[int | None] = mapped_column(Integer)

    base_pcb_notes: Mapped[str | None] = mapped_column(Text)
    base_mfg_notes: Mapped[str | None] = mapped_column(Text)

    classifications: Mapped[list[Classification]] = relationship(
        secondary=assembly_classifications, back_populates="assemblies"
    )
    jobs: Mapped[list[Job]] = relationship(
        back_populates="assembly", cascade="all, delete-orphan"
    )


class Job(Base, TimestampMixin):
    __tablename__ = "jobs"
    __table_args__ = (
        # Partial unique index: superseded OR discarded jobs free their identity
        # slot so a future ingest of the same identity can land as a new active Job.
        # The predicate must include discarded_at IS NULL (Phase 15 §5.2); without
        # it a discarded job retains its slot and the next ingest UNIQUE-violates.
        Index(
            "ix_job_identity_active",
            "assembly_id", "build_type", "split_suffix",
            "repeat_reference", "build_qualifier",
            unique=True,
            sqlite_where=text("superseded_at IS NULL AND discarded_at IS NULL"),
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)

    assembly_id: Mapped[int] = mapped_column(
        ForeignKey("assemblies.id", ondelete="RESTRICT"), nullable=False
    )
    customer_id: Mapped[int] = mapped_column(
        ForeignKey("customers.id", ondelete="RESTRICT"), nullable=False
    )
    salesperson_id: Mapped[int | None] = mapped_column(
        ForeignKey("salespeople.id", ondelete="SET NULL")
    )

    split_suffix: Mapped[str | None] = mapped_column(String(32))
    repeat_reference: Mapped[str | None] = mapped_column(String(32))
    build_type: Mapped[BuildType | None] = mapped_column(Enum(BuildType, name="build_type"))
    build_qualifier: Mapped[BuildQualifier | None] = mapped_column(
        Enum(BuildQualifier, name="build_qualifier"), nullable=True
    )
    status: Mapped[JobStatus] = mapped_column(
        Enum(JobStatus, name="job_status"), default=JobStatus.planned, nullable=False
    )

    quantity: Mapped[int] = mapped_column(Integer, nullable=False)

    ship_date_text: Mapped[str | None] = mapped_column(String(5))
    ship_lead_time_raw: Mapped[str | None] = mapped_column(String(16))
    resolved_ship_date: Mapped[date | None] = mapped_column(Date)
    shipped_at: Mapped[date | None] = mapped_column(Date)
    ever_shipped_at: Mapped[date | None] = mapped_column(Date, nullable=True)

    ship_method: Mapped[str | None] = mapped_column(String(64))
    smt_feeder_count: Mapped[int | None] = mapped_column(Integer)

    doc_released_at: Mapped[date | None] = mapped_column(Date)
    kit_released_at: Mapped[date | None] = mapped_column(Date)

    wip_status_note: Mapped[str | None] = mapped_column(Text)
    wip_expected_clear_date: Mapped[date | None] = mapped_column(Date)

    notes_clear_date_raw: Mapped[str | None] = mapped_column(String(16))

    run_cost: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))

    bom_compare_photos: Mapped[str | None] = mapped_column(Text)

    run_pcb_notes: Mapped[str | None] = mapped_column(Text)
    run_mfg_notes: Mapped[str | None] = mapped_column(Text)
    kit_notes: Mapped[str | None] = mapped_column(Text)
    scheduling_notes: Mapped[str | None] = mapped_column(Text)

    line_1: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    line_2: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    line_3: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    superseded_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    superseded_by_batch_id: Mapped[int | None] = mapped_column(
        ForeignKey("import_batches.id", ondelete="RESTRICT"), nullable=True
    )
    discarded_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    assembly: Mapped[Assembly] = relationship(back_populates="jobs")
    customer: Mapped[Customer] = relationship(back_populates="jobs")
    salesperson: Mapped[Salesperson | None] = relationship(back_populates="jobs")


class ImportBatch(Base, TimestampMixin):
    __tablename__ = "import_batches"

    id: Mapped[int] = mapped_column(primary_key=True)
    source_file: Mapped[str] = mapped_column(String(255), nullable=False)
    source_sha256: Mapped[str | None] = mapped_column(String(64))
    row_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    status: Mapped[ImportStatus] = mapped_column(
        Enum(ImportStatus, name="import_batch_status"),
        default=ImportStatus.pending,
        nullable=False,
    )
    sheet_kind: Mapped[SheetKind] = mapped_column(
        Enum(SheetKind, name="sheet_kind"),
        nullable=False,
        server_default="live",
    )
    notes: Mapped[str | None] = mapped_column(Text)

    rows: Mapped[list[ImportStagingRow]] = relationship(
        back_populates="batch", cascade="all, delete-orphan"
    )


class ImportStagingRow(Base, TimestampMixin):
    __tablename__ = "import_staging"
    __table_args__ = (
        Index("ix_import_staging_batch_id", "batch_id"),
        Index("ix_import_staging_discarded_at", "discarded_at"),
        Index("ix_import_staging_dup_group", "batch_id", "duplicate_group_key"),
        Index("ix_staging_batch_review_status", "batch_id", "review_status"),
        Index("ix_staging_batch_parsed_pn", "batch_id", "parsed_part_number"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    batch_id: Mapped[int] = mapped_column(
        ForeignKey("import_batches.id", ondelete="CASCADE"), nullable=False
    )
    source_row_number: Mapped[int] = mapped_column(Integer, nullable=False)

    raw_shipped: Mapped[str | None] = mapped_column(Text)
    raw_pcb_notes: Mapped[str | None] = mapped_column(Text)
    raw_kit_notes: Mapped[str | None] = mapped_column(Text)
    raw_scheduling_notes: Mapped[str | None] = mapped_column(Text)
    raw_line_1: Mapped[str | None] = mapped_column(Text)
    raw_line_2: Mapped[str | None] = mapped_column(Text)
    raw_line_3: Mapped[str | None] = mapped_column(Text)
    raw_job: Mapped[str | None] = mapped_column(Text)
    raw_qty: Mapped[str | None] = mapped_column(Text)
    raw_ship_date: Mapped[str | None] = mapped_column(Text)
    raw_prog: Mapped[str | None] = mapped_column(Text)
    raw_mfg_notes: Mapped[str | None] = mapped_column(Text)
    raw_smt_lines: Mapped[str | None] = mapped_column(Text)
    raw_smt_plcmnts: Mapped[str | None] = mapped_column(Text)
    raw_ship_method: Mapped[str | None] = mapped_column(Text)
    raw_customer: Mapped[str | None] = mapped_column(Text)
    raw_sales_p: Mapped[str | None] = mapped_column(Text)
    raw_doc_rel: Mapped[str | None] = mapped_column(Text)
    raw_kit_rel: Mapped[str | None] = mapped_column(Text)
    raw_code: Mapped[str | None] = mapped_column(Text)
    raw_bom_compare_photos: Mapped[str | None] = mapped_column(Text)
    build_qualifier: Mapped[BuildQualifier | None] = mapped_column(
        Enum(BuildQualifier, name="build_qualifier"), nullable=True
    )

    processing_status: Mapped[ImportStatus] = mapped_column(
        Enum(ImportStatus, name="import_row_status"),
        default=ImportStatus.pending,
        nullable=False,
    )
    processing_error: Mapped[str | None] = mapped_column(Text)
    suggested_correction: Mapped[str | None] = mapped_column(Text)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime)
    resolved_job_id: Mapped[int | None] = mapped_column(
        ForeignKey("jobs.id", ondelete="SET NULL")
    )
    discarded_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    duplicate_group_key: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Review-tracking columns (Phase 18a).  All nullable; populated only when
    # the staging row passes through the review surface.
    # Deferred so that historical migrations using sa.select(ImportStagingRow)
    # do not fail with "no such column" before migration 0009 has been applied.
    original_raw_job: Mapped[str | None] = deferred(mapped_column(Text, nullable=True))
    # review_status values: 'pending' | 'verified' | 'edited' | 'deleted'
    review_status: Mapped[str | None] = deferred(mapped_column(Text, nullable=True))
    # reviewed_by is a placeholder ('frontend') until operator auth lands in Phase 18b.
    reviewed_by: Mapped[str | None] = deferred(mapped_column(Text, nullable=True))
    reviewed_at: Mapped[datetime | None] = deferred(mapped_column(DateTime, nullable=True))
    review_part_number_override: Mapped[str | None] = deferred(mapped_column(Text, nullable=True))
    review_split_suffix_override: Mapped[str | None] = deferred(mapped_column(Text, nullable=True))
    # Populated at Stage 3.5 (classify_new_parts_for_review) post-migration 0010.
    # Enables indexed read path in _rows_for_pn instead of Python-side decomposition.
    parsed_part_number: Mapped[str | None] = deferred(mapped_column(Text, nullable=True))

    batch: Mapped[ImportBatch] = relationship(back_populates="rows")


class JobSupersessionCandidate(Base, TimestampMixin):
    """Pending or resolved supersession candidate for operator review.

    Invariants (enforced by service layer):
    - resolution IS NULL iff resolved_at IS NULL
    - closed_by_shield_reason IS NOT NULL => resolution = CandidateResolution.reject
    - superseded_at IS NULL iff superseded_by_batch_id IS NULL (on Job)

    The partial unique index ix_candidate_pending_unique enforces at most one
    pending candidate per job at the database level.
    """

    __tablename__ = "job_supersession_candidate"
    __table_args__ = (
        # Belt-and-suspenders: at most one pending candidate per job.
        Index(
            "ix_candidate_pending_unique",
            "job_id",
            unique=True,
            sqlite_where=text("resolved_at IS NULL"),
        ),
        Index("ix_candidate_resolved_at", "resolved_at"),
        Index("ix_candidate_detected_batch", "detected_in_batch_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    job_id: Mapped[int] = mapped_column(
        ForeignKey("jobs.id", ondelete="RESTRICT"), nullable=False
    )
    detected_in_batch_id: Mapped[int] = mapped_column(
        ForeignKey("import_batches.id", ondelete="RESTRICT"), nullable=False
    )
    reason: Mapped[CandidateReason] = mapped_column(
        Enum(CandidateReason, name="candidate_reason"), nullable=False
    )
    detected_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    resolution: Mapped[CandidateResolution | None] = mapped_column(
        Enum(CandidateResolution, name="candidate_resolution"), nullable=True
    )
    closed_by_shield_reason: Mapped[str | None] = mapped_column(String(64), nullable=True)

    job: Mapped[Job] = relationship()
    detected_in_batch: Mapped[ImportBatch] = relationship(
        foreign_keys=[detected_in_batch_id]
    )
