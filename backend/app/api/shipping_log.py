from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from datetime import datetime, tzinfo
from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, StrictInt
from sqlalchemy.exc import DBAPIError, SQLAlchemyError
from sqlalchemy.orm import Session

from ..config import Settings, get_settings
from ..schemas import (
    ShippingLogCandidateList,
    ShippingLogErrorBody,
    ShippingLogIneligibleBody,
    ShippingLogSelectionSizeBody,
)
from ..services import shipping_log as shipping_log_service
from ..services.shipping_log import (
    SHIPPING_LOG_MEDIA_TYPE,
    IneligibleJobs,
    SelectionSizeInvalid,
    ShippingLogFile,
    TemplateReady,
    TemplateUnavailable,
    TemplateUnavailableFailure,
    WorkbookStampingError,
)
from .deps import get_session, get_wall_clock

logger = logging.getLogger(__name__)

router = APIRouter()

SQLITE_MAX_INTEGER = 2**63 - 1

# Read once at import from the Settings the app starts with. It lets Pydantic
# reject an oversized array before generate_shipping_log builds a set from it;
# the exact bound, shipping_log_max_jobs, applies after de-duplication.
JOB_IDS_PARSE_CEILING = get_settings().shipping_log_max_jobs * 4


class ShippingLogRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # Bounded to SQLite's INTEGER range: a larger int overflows the driver's
    # parameter binding instead of reporting NOT_FOUND.
    job_ids: list[Annotated[StrictInt, Field(ge=1, le=SQLITE_MAX_INTEGER)]] = Field(
        max_length=JOB_IDS_PARSE_CEILING,
    )


_template_install_lock = threading.Lock()


def get_shipping_log_template(request: Request) -> TemplateReady | TemplateUnavailable:
    """The template application_lifespan loaded before the app accepted requests.

    The locked fallback covers an app served without its lifespan (a TestClient
    used outside a `with` block). It loads, logs and caches the same way, once.
    """
    template = getattr(request.app.state, "shipping_log_template", None)
    if template is None:
        with _template_install_lock:
            template = getattr(request.app.state, "shipping_log_template", None)
            if template is None:
                template = shipping_log_service.load_bundled_shipping_log_template()
                request.app.state.shipping_log_template = template
    return template


@router.get("/candidates", response_model=ShippingLogCandidateList)
def list_shipping_log_candidates(
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
    template: TemplateReady | TemplateUnavailable = Depends(get_shipping_log_template),
):
    """List the planned jobs a shipping log may be generated from.

    The population is the Shipping view's, in its order, capped at
    shipping_log_candidate_max with the cap reported through `truncated`.
    No authorization gate: GET /api/jobs/shipping already serves the same data
    to LAN clients.
    """
    return shipping_log_service.list_shipping_log_candidates(session, settings, template)


@router.post(
    "",
    response_class=Response,
    responses={
        200: {
            "description": "The shipping log workbook. The filename is in Content-Disposition; "
                           "X-Shipping-Log-Clipped lists job IDs whose notes won't print in full.",
            "content": {SHIPPING_LOG_MEDIA_TYPE: {"schema": {"type": "string", "format": "binary"}}},
        },
        409: {"model": ShippingLogIneligibleBody, "description": "At least one job is not eligible."},
        422: {
            "model": ShippingLogSelectionSizeBody,
            "description": "selection_size; a body that fails the schema gets FastAPI's default 422 body.",
        },
        500: {"model": ShippingLogErrorBody, "description": "Database or workbook stamping failure."},
        503: {"model": ShippingLogErrorBody, "description": "The template is missing from this build."},
    },
)
def create_shipping_log(
    body: ShippingLogRequest,
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
    template: TemplateReady | TemplateUnavailable = Depends(get_shipping_log_template),
    now_in: Callable[[tzinfo], datetime] = Depends(get_wall_clock),
) -> Response:
    """Generate a shipping log workbook for the selected planned jobs.

    Read-only: no job state changes. All-or-nothing: any ineligible ID fails the
    whole request with every offending ID listed. A sync handler on purpose:
    openpyxl is CPU-bound and runs in the threadpool, off the event loop.

    200 the .xlsx.
    409 { kind: "ineligible_jobs", jobs: [{ job_id, reason }] }.
    422 { kind: "selection_size", requested, max }, or FastAPI's default body.
    500 { kind: "internal" }; logged with job count and template SHA-256 only.
    503 { kind: "template_unavailable" }; the reason is in the startup log only,
        because it can contain filesystem paths.
    """
    try:
        outcome = shipping_log_service.generate_shipping_log(
            body.job_ids, session, settings, template, now_in,
        )
    except SQLAlchemyError as exc:
        driver_message = str(exc.orig) if isinstance(exc, DBAPIError) else ""
        _log_generation_failure(f"database {type(exc).__name__} {driver_message}".strip(), body, template)
        return JSONResponse(status_code=500, content={"kind": "internal"})
    except WorkbookStampingError as exc:
        _log_generation_failure(f"stamping {exc.stage} {exc.cause_type}", body, template)
        return JSONResponse(status_code=500, content={"kind": "internal"})

    if isinstance(outcome, ShippingLogFile):
        headers = {
            "Content-Disposition": f'attachment; filename="{outcome.filename}"',
            # An .xlsx is already deflated. Declaring identity also keeps
            # GZipMiddleware from replacing Content-Length with a compressed one.
            "Content-Encoding": "identity",
        }
        if outcome.clipped_job_ids:
            headers["X-Shipping-Log-Clipped"] = ",".join(str(job_id) for job_id in outcome.clipped_job_ids)
        return Response(content=outcome.content, media_type=SHIPPING_LOG_MEDIA_TYPE, headers=headers)

    if isinstance(outcome, TemplateUnavailableFailure):
        return JSONResponse(status_code=503, content={"kind": "template_unavailable"})
    if isinstance(outcome, SelectionSizeInvalid):
        return JSONResponse(
            status_code=422,
            content={"kind": "selection_size", "requested": outcome.requested, "max": outcome.max},
        )
    if isinstance(outcome, IneligibleJobs):
        return JSONResponse(
            status_code=409,
            content={
                "kind": "ineligible_jobs",
                "jobs": [{"job_id": job.job_id, "reason": job.reason.value} for job in outcome.jobs],
            },
        )
    raise AssertionError(f"unhandled shipping log outcome: {type(outcome).__name__}")


def _log_generation_failure(
    failure: str,
    body: ShippingLogRequest,
    template: TemplateReady | TemplateUnavailable,
) -> None:
    # Counts and hashes only. No note text or part numbers: openpyxl and driver
    # tracebacks can quote cell values, so no exc_info either.
    logger.error(
        "Shipping log generation failed: %s (jobs=%d, template_sha256=%s)",
        failure, len(set(body.job_ids)), template.sha256,
    )
