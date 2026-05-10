from __future__ import annotations

from copy import deepcopy
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, computed_field, create_model, field_serializer, model_validator

from .errors import resolve_highlight_fields
from .models import BuildType, BuildQualifier, CandidateReason, CandidateResolution, ImportStatus, JobStatus, SheetKind


_MARKDOWN_NOTE = "CommonMark-annotated Markdown. Client must render via a Markdown library (e.g. marked)."


def partial_model(model: type[BaseModel]) -> type[BaseModel]:
    """Return a copy of `model` with every field made Optional with default None."""
    fields: dict[str, Any] = {}
    for name, field_info in model.model_fields.items():
        new_field = deepcopy(field_info)
        new_field.default = None
        new_field.default_factory = None
        annotation = field_info.annotation
        if annotation is not None:
            annotation = annotation | None
        fields[name] = (annotation, new_field)
    return create_model(
        f"{model.__name__}Partial",
        __base__=BaseModel,
        **fields,
    )

class _ORMModel(BaseModel):

    model_config = ConfigDict(from_attributes=True)


# ---- customers ---------------------------------------------------------------


class CustomerBase(BaseModel):
    name: str = Field(min_length=1, max_length=128)


class CustomerCreate(CustomerBase):
    pass


CustomerUpdate = partial_model(CustomerBase)


class CustomerRead(_ORMModel, CustomerBase):
    id: int
    created_at: datetime
    updated_at: datetime


# ---- salespeople -------------------------------------------------------------


class SalespersonBase(BaseModel):
    code: str = Field(min_length=1, max_length=16)
    name: str | None = Field(default=None, max_length=128)


class SalespersonCreate(SalespersonBase):
    pass


SalespersonUpdate = partial_model(SalespersonBase)


class SalespersonRead(_ORMModel, SalespersonBase):
    id: int
    created_at: datetime
    updated_at: datetime


# ---- classifications ---------------------------------------------------------


class ClassificationBase(BaseModel):
    code: str = Field(min_length=1, max_length=32)
    description: str | None = Field(default=None, max_length=255)


class ClassificationCreate(ClassificationBase):
    pass


ClassificationUpdate = partial_model(ClassificationBase)


class ClassificationRead(_ORMModel, ClassificationBase):
    id: int
    created_at: datetime
    updated_at: datetime


# ---- assemblies --------------------------------------------------------------


class AssemblyBase(BaseModel):
    part_number: str = Field(min_length=1, max_length=64)
    program_name: str | None = Field(default=None, max_length=64)
    smt_placements: int | None = Field(default=None, ge=0)
    base_pcb_notes: str | None = Field(default=None, description=_MARKDOWN_NOTE)
    base_mfg_notes: str | None = Field(default=None, description=_MARKDOWN_NOTE)


class AssemblyCreate(AssemblyBase):
    classification_codes: list[str] = Field(default_factory=list)


AssemblyUpdate = create_model(
    "AssemblyUpdate",
    __base__=partial_model(AssemblyBase),
    classification_codes=(list[str] | None, None),
)


class AssemblyRead(_ORMModel, AssemblyBase):
    id: int
    created_at: datetime
    updated_at: datetime
    classifications: list[ClassificationRead] = Field(default_factory=list)


class AssemblyWithJobs(AssemblyRead):
    jobs: list[JobRead] = Field(default_factory=list)


# ---- jobs --------------------------------------------------------------------


class JobBase(BaseModel):
    assembly_id: int
    customer_id: int
    salesperson_id: int | None = None

    split_suffix: str | None = Field(default=None, max_length=32)
    repeat_reference: str | None = Field(default=None, max_length=32)
    build_type: BuildType | None = None
    status: JobStatus = JobStatus.planned

    quantity: int = Field(ge=1)

    ship_date_text: str | None = Field(default=None, max_length=5)
    ship_lead_time_raw: str | None = Field(default=None, max_length=16)
    resolved_ship_date: date | None = None
    shipped_at: date | None = None

    ship_method: str | None = Field(default=None, max_length=64)
    smt_feeder_count: int | None = Field(default=None, ge=0)

    doc_released_at: date | None = None
    kit_released_at: date | None = None

    wip_status_note: str | None = None
    wip_expected_clear_date: date | None = None

    notes_clear_date_raw: str | None = Field(default=None, max_length=16)

    run_cost: Decimal | None = Field(default=None, ge=Decimal("0"))

    bom_compare_photos: str | None = None
    run_pcb_notes: str | None = Field(default=None, description=_MARKDOWN_NOTE)
    run_mfg_notes: str | None = Field(default=None, description=_MARKDOWN_NOTE)
    kit_notes: str | None = Field(default=None, description=_MARKDOWN_NOTE)
    scheduling_notes: str | None = Field(default=None, description=_MARKDOWN_NOTE)

    line_1: bool = False
    line_2: bool = False
    line_3: bool = False


class JobCreate(JobBase):
    pass


JobUpdate = partial_model(JobBase)


class JobRead(_ORMModel, JobBase):
    id: int
    created_at: datetime
    updated_at: datetime
    discarded_at: datetime | None = None

    @field_serializer("run_cost")
    def _serialize_run_cost(self, v: Decimal | None) -> float | None:
        return float(v) if v is not None else None


class JobReadExpanded(JobRead):
    assembly: AssemblyRead
    customer: CustomerRead
    salesperson: SalespersonRead | None = None
    build_qualifier: BuildQualifier | None = None


# ---- import models -----------------------------------------------------------


class ImportBatchRead(_ORMModel):
    id: int
    source_file: str
    source_sha256: str | None
    row_count: int
    status: ImportStatus
    sheet_kind: SheetKind
    notes: str | None
    created_at: datetime
    updated_at: datetime


class ImportStagingRowRead(_ORMModel):
    id: int
    batch_id: int
    source_row_number: int
    processing_status: ImportStatus
    processing_error: str | None
    suggested_correction: str | None
    resolved_job_id: int | None
    processed_at: datetime | None
    discarded_at: datetime | None = None
    duplicate_group_key: str | None = None
    created_at: datetime
    updated_at: datetime


class _StagingRawFields(BaseModel):
    raw_shipped: str | None = None
    raw_pcb_notes: str | None = None
    raw_kit_notes: str | None = None
    raw_scheduling_notes: str | None = None
    raw_line_1: str | None = None
    raw_line_2: str | None = None
    raw_line_3: str | None = None
    raw_job: str | None = None
    raw_qty: str | None = None
    raw_ship_date: str | None = None
    raw_prog: str | None = None
    raw_mfg_notes: str | None = None
    raw_smt_lines: str | None = None
    raw_smt_plcmnts: str | None = None
    raw_ship_method: str | None = None
    raw_customer: str | None = None
    raw_sales_p: str | None = None
    raw_doc_rel: str | None = None
    raw_kit_rel: str | None = None
    raw_code: str | None = None
    raw_bom_compare_photos: str | None = None


class StagingRowDetailRead(ImportStagingRowRead, _StagingRawFields):
    build_qualifier: BuildQualifier | None = None

    @computed_field
    @property
    def highlight_fields(self) -> list[str]:
        return resolve_highlight_fields(self.processing_error)


class StagingRowCorrectionRequest(_StagingRawFields):
    model_config = ConfigDict(extra="forbid")


# ---- conflict group ----------------------------------------------------------


class ConflictKind(str, Enum):
    intra_file_duplicate = "intra_file_duplicate"


class ConflictGroup(BaseModel):
    batch_id: int
    group_key: str
    kind: ConflictKind
    rows: list[StagingRowDetailRead]
    # NB: rows is invariant len >= 2 by construction (see list_conflicts builder)


# ---- restore-conflict preview (Phase 15 Epoch 2) ----------------------------


class RestoreSourceKind(str, Enum):
    """Discriminates whether a restore candidate is a staging row or a persisted job."""
    STAGING = "staging"
    JOB = "job"


class IncomingRestoreCandidate(BaseModel):
    """The row the operator wants to restore, discriminated by source kind."""
    kind: RestoreSourceKind
    staging: StagingRowDetailRead | None = None
    job: JobReadExpanded | None = None


class RestoreConflictPreview(BaseModel):
    """Read-only snapshot describing what would collide if a discarded row were restored.

    Pre:  the target row exists and discarded_at IS NOT NULL.
    Post: purely descriptive — calling the preview endpoint never mutates state.
    """
    incoming: IncomingRestoreCandidate
    colliding_staging_errored_rows: list[StagingRowDetailRead]
    colliding_staging_discarded_rows: list[StagingRowDetailRead]
    colliding_live_jobs: list[JobReadExpanded]
    group_key: str


class StagingRestoreAction(BaseModel):
    """One operator-resolution action for a staging-side row in the preview modal."""
    kind: str  # "edit" | "discard"
    row_id: int
    payload: dict[str, Any] | None = None


class StagingRestoreRequest(BaseModel):
    """Request body for POST /api/staging/{rowId}/restore.

    `actions: []` is the no-conflict path — restore is attempted directly.
    """
    actions: list[StagingRestoreAction] = Field(default_factory=list)


class JobRestoreRequest(BaseModel):
    """Request body for POST /api/jobs/{jobId}/restore.

    Symmetric with StagingRestoreRequest (§6.2). Every action references a
    staging row id (the operator cannot edit live-job colliders).
    `actions: []` is the no-conflict path — restore is attempted directly.
    """
    actions: list[StagingRestoreAction] = Field(default_factory=list)


# ---- supersession ------------------------------------------------------------


class JobSupersessionCandidateRead(_ORMModel):
    id: int
    job_id: int
    detected_in_batch_id: int
    reason: CandidateReason
    detected_at: datetime
    resolved_at: datetime | None
    resolution: CandidateResolution | None
    closed_by_shield_reason: str | None
    created_at: datetime
    updated_at: datetime


class JobSupersessionCandidatePage(BaseModel):
    items: list[JobSupersessionCandidateRead]
    total: int


class SupersessionApprovalRequest(BaseModel):
    pass


class SupersessionRejectionRequest(BaseModel):
    pass


class SupersessionBulkApprovalRequest(BaseModel):
    ids: list[int] = Field(min_length=1)


class BulkApprovalResultRead(BaseModel):
    approved: list[int]
    shield_rejected: list[int]
    already_closed: list[int]
    not_found: list[int]


# ---- history edit / discard --------------------------------------------------


class HistoryJobEditRequest(BaseModel):
    """Edit reconciliation-style fields of a shipped job.

    Five discrete identity fields replace the old raw_job free-text field (Patch 01).
    Three ship-time fields retain Phase 17 semantics (is-not-None check; empty rejected).
    At least one editable field must be present in the request body; a body containing
    only `reason` is rejected (422) by the model validator.

    extra="forbid": stale clients that still send the removed `raw_job` key receive an
    explicit "extra fields not permitted" 422 rather than a silent drop.
    """

    model_config = ConfigDict(extra="forbid")

    # Identity fields — PATCH semantics; absent key vs null/empty are distinguished
    # by consulting model_fields_set in the service layer.
    part_number:      str | None = None
    build_type:       str | None = None
    split_suffix:     str | None = None
    repeat_reference: str | None = None
    build_qualifier:  str | None = None

    # Ship-time fields — Phase 17 semantics (is-not-None check); empty rejected downstream.
    raw_qty:      str | None = None
    raw_customer: str | None = None
    raw_shipped:  str | None = None

    reason: str = Field(min_length=1, max_length=500)

    @model_validator(mode="after")
    def _require_at_least_one_editable_field(self) -> "HistoryJobEditRequest":
        identity_present = any(
            f in self.model_fields_set
            for f in ("part_number", "build_type", "split_suffix",
                      "repeat_reference", "build_qualifier")
        )
        ship_present = any(
            f in self.model_fields_set
            for f in ("raw_qty", "raw_customer", "raw_shipped")
        )
        if not (identity_present or ship_present):
            raise ValueError("At least one editable field must be provided.")
        return self


class JobDiscardRequest(BaseModel):
    """Request body for POST /api/jobs/{jobId}/discard.

    `reason` is required (min 1, max 500 chars), written to logger.info,
    and not persisted.
    """

    reason: str = Field(min_length=1, max_length=500)
