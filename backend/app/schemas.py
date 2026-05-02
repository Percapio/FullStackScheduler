from __future__ import annotations

from copy import deepcopy
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, computed_field, create_model, field_serializer

from .errors import resolve_highlight_fields
from .models import BuildType, ImportStatus, JobStatus


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

    @field_serializer("run_cost")
    def _serialize_run_cost(self, v: Decimal | None) -> float | None:
        return float(v) if v is not None else None


class JobReadExpanded(JobRead):
    assembly: AssemblyRead
    customer: CustomerRead
    salesperson: SalespersonRead | None = None


# ---- import models -----------------------------------------------------------


class ImportBatchRead(_ORMModel):
    id: int
    source_file: str
    source_sha256: str | None
    row_count: int
    status: ImportStatus
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
    @computed_field
    @property
    def highlight_fields(self) -> list[str]:
        return resolve_highlight_fields(self.processing_error)


class StagingRowCorrectionRequest(_StagingRawFields):
    model_config = ConfigDict(extra="forbid")
