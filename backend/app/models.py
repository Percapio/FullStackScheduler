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
from sqlalchemy import select
from sqlalchemy.orm import (
    DeclarativeBase,
    Mapped,
    column_property,
    deferred,
    mapped_column,
    relationship,
)


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
        Index(
            "ix_job_active_planned",
            "status",
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

    discarded_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    # ---- 2nd OPS (Phase 22) --------------------------------------------------
    # There is no stored status. NULL reviewed_at means never audited; the
    # not_applicable / recorded split derives from the line count and this note,
    # so no invariant binds two tables together.
    second_ops_reviewed_at: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True
    )
    second_ops_unexpected_inclusions: Mapped[str | None] = mapped_column(
        Text, nullable=True
    )

    assembly: Mapped[Assembly] = relationship(back_populates="jobs")
    customer: Mapped[Customer] = relationship(back_populates="jobs")
    salesperson: Mapped[Salesperson | None] = relationship(back_populates="jobs")
    second_ops_lines: Mapped[list[JobSecondOpsLine]] = relationship(
        back_populates="job",
        cascade="all, delete-orphan",
        order_by="JobSecondOpsLine.line_order",
    )


class JobSecondOpsLine(Base, TimestampMixin):
    """Component-level Audit BOM transcription for one job. One row per BOM line.

    All eight source fields are stored verbatim as text: nothing sums, sorts or
    compares them, and operators type values like '40 ea' by hand, so a numeric
    parse would add a failure path and buy no capability.

    find_number is a source-sheet ordinal, not an identity — it repeats across
    jobs and can repeat within one paste. The surrogate PK plus line_order is
    what orders and identifies rows.

    component_part_number is deliberately not named part_number:
    Assembly.part_number is the assembled board, this is a component on it.

    Every column is bounded, ref_des included. ref_des holds a comma-joined
    designator list and is the one field with a plausible case for Text — which
    is exactly what would make the per-field width enforcement in
    validate_second_ops_payload vacuous for the field most likely to carry a
    large paste. The longest Ref_Des observed in B142006 AUDIT BOM.xlsx is 131
    characters; 2048 is ~15x that.
    """

    __tablename__ = "job_second_ops_lines"
    __table_args__ = (
        Index("ix_second_ops_job_order", "job_id", "line_order"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    job_id: Mapped[int] = mapped_column(
        ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False
    )
    line_order: Mapped[int] = mapped_column(Integer, nullable=False)

    find_number: Mapped[str | None] = mapped_column(String(32))
    component_part_number: Mapped[str | None] = mapped_column(String(128))
    per_board_count: Mapped[str | None] = mapped_column(String(32))
    ref_des: Mapped[str | None] = mapped_column(String(2048))
    description: Mapped[str | None] = mapped_column(String(255))
    mount_type: Mapped[str | None] = mapped_column(String(16))
    quantity_needed: Mapped[str | None] = mapped_column(String(32))
    quantity_on_hand: Mapped[str | None] = mapped_column(String(32))

    job: Mapped[Job] = relationship(back_populates="second_ops_lines")


# Correlated scalar COUNT over job_second_ops_lines, deferred so it is absent
# from the ~500-row grid loads and every other Job select. Undeferred explicitly
# by the export stream, which yields single rows and has no page to batch; the
# summary assembler counts in its own GROUP BY instead, so a caller who forgets
# the undefer cannot silently produce an N+1 there.
Job.second_ops_line_count = column_property(
    select(func.count(JobSecondOpsLine.id))
    .where(JobSecondOpsLine.job_id == Job.id)
    .correlate_except(JobSecondOpsLine)
    .scalar_subquery(),
    deferred=True,
)


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



