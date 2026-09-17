import math
import time
from typing import Literal

from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from ..config import Settings, get_settings
from ..services.shipping_photos import (
    PHOTO_FOLDER_PATTERN,
    PhotoDirectoryStatus,
    PhotoFolderIndex,
    RateLimited,
    open_photo_folder,
    probe_missing_folders,
    resolve_folder_index,
)

router = APIRouter()

class PhotoFolderIndexRead(BaseModel):
    status: Literal["unconfigured", "unavailable", "ok"]
    folders: list[str]
    truncated: bool

@router.get("/available-dates", response_model=PhotoFolderIndexRead)
def get_available_dates(
    probe: list[str] = Query(default=[]),
    settings: Settings = Depends(get_settings)
) -> PhotoFolderIndexRead:
    idx = resolve_folder_index(settings, time.monotonic)
    
    if idx.status == PhotoDirectoryStatus.OK and probe:
        # Probe miss logic
        folders = probe_missing_folders(set(probe), idx, settings)
    else:
        folders = idx.folder_names
        
    return PhotoFolderIndexRead(
        status=idx.status.value,
        folders=sorted(folders) if idx.status == PhotoDirectoryStatus.OK else [],
        truncated=idx.truncated
    )

class PhotoOpenRequest(BaseModel):
    date_folder: str = Field(pattern=PHOTO_FOLDER_PATTERN.pattern)

from .deps import require_loopback, is_loopback_caller

@router.post("/open", dependencies=[Depends(require_loopback)])
def open_photo_folder_endpoint(
    req: PhotoOpenRequest,
    settings: Settings = Depends(get_settings)
):
    res = open_photo_folder(req.date_folder, settings, time.monotonic)
    
    if res[0] == "ok":
        return {"opened": res[1]}
        
    failure = res[1]
    
    if failure == "unconfigured":
        return JSONResponse(status_code=409, content={"kind": "unconfigured"})
    elif failure == "unavailable":
        return JSONResponse(status_code=409, content={"kind": "unavailable"})
    elif failure == "invalid_name":
        # Should be caught by Pydantic pattern, but just in case
        return JSONResponse(status_code=422, content={"kind": "invalid_name"})
    elif failure == "not_found":
        return JSONResponse(status_code=404, content={"kind": "not_found", "date_folder": req.date_folder})
    elif failure == "shell_error":
        return JSONResponse(status_code=500, content={"kind": "shell_error"})
    elif isinstance(failure, RateLimited):
        wait = max(1, math.ceil(failure.remaining_seconds))
        return JSONResponse(
            status_code=429,
            content={"kind": "rate_limited", "retry_after_seconds": wait},
            headers={"Retry-After": str(wait)}
        )

from typing import List, Optional
import threading
from fastapi import Request
from fastapi.responses import FileResponse, StreamingResponse
from ..services.photo_files import (
    resolve_file_index, resolve_photo_file_path, PhotoFileListStatus
)
from ..services.photo_thumbnails import generate_once, acquire_thumbnail_permit

class PhotoFileEntryRead(BaseModel):
    name: str
    size_bytes: int
    mtime_ns: int
    version: str
    previewable: bool

class PhotoFileListRead(BaseModel):
    status: Literal["unconfigured", "unavailable", "not_found", "ok"]
    folders: List[str]
    entries: List[PhotoFileEntryRead]
    truncated: bool
    folders_truncated: bool

@router.get("/files", response_model=PhotoFileListRead)
def list_files(
    date_folder: str = Query(..., pattern=PHOTO_FOLDER_PATTERN.pattern),
    sub_folder: str = Query(default="", max_length=255, pattern="^[^/\\\\]*$"),
    settings: Settings = Depends(get_settings)
):
    idx = resolve_file_index(date_folder, sub_folder, settings, time.monotonic)
    try:
        from ..services.photo_warm import enqueue_warm
        enqueue_warm(date_folder, sub_folder, settings)
    except Exception as e:
        import logging
        logging.getLogger(__name__).error(f"Failed to enqueue warm worker: {e}")
        
    entries = []
    if idx.status == PhotoFileListStatus.OK:
        entries = [
            PhotoFileEntryRead(
                name=e.name,
                size_bytes=e.size_bytes,
                mtime_ns=e.mtime_ns,
                version=e.version,
                previewable=e.previewable
            ) for e in idx.entries
        ]
    return PhotoFileListRead(
        status=idx.status.value,
        folders=idx.folders if idx.status == PhotoFileListStatus.OK else [],
        entries=entries,
        truncated=idx.truncated,
        folders_truncated=idx.folders_truncated if idx.status == PhotoFileListStatus.OK else False
    )

@router.get("/file/{filename}")
def get_file(
    filename: str,
    date_folder: str = Query(..., pattern=PHOTO_FOLDER_PATTERN.pattern),
    sub_folder: str = Query(default="", max_length=255, pattern="^[^/\\\\]*$"),
    settings: Settings = Depends(get_settings)
):
    idx = resolve_file_index(date_folder, sub_folder, settings, time.monotonic)
    res = resolve_photo_file_path(date_folder, sub_folder, filename, idx, settings)
    
    if res[0] == "err":
        return JSONResponse(status_code=404, content={"kind": res[1]})
        
    return FileResponse(
        res[1],
        headers={
            "Cache-Control": "private, max-age=31536000, immutable",
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": "sandbox",
            "Content-Encoding": "identity"
        },
    )


from ..services.photo_thumbnails import generate_once

@router.get("/thumb/{filename}")
def get_thumb(
    filename: str,
    date_folder: str = Query(..., pattern=PHOTO_FOLDER_PATTERN.pattern),
    sub_folder: str = Query(default="", max_length=255, pattern="^[^/\\\\]*$"),
    settings: Settings = Depends(get_settings)
):
    idx = resolve_file_index(date_folder, sub_folder, settings, time.monotonic)
    
    res = generate_once(date_folder, sub_folder, filename, idx, "interactive", settings)
    if res[0] == "err":
        if res[1] == "not_previewable":
            return JSONResponse(status_code=415, content={"kind": res[1]}, headers={"Cache-Control": "no-store"})
        elif res[1] in ("unavailable", "cache_unavailable", "saturated", "timeout"):
            return JSONResponse(status_code=503, content={"kind": res[1]}, headers={"Retry-After": "1", "Cache-Control": "no-store"})
        else:
            return JSONResponse(status_code=404, content={"kind": res[1]}, headers={"Cache-Control": "no-store"})
            
    return FileResponse(
        res[1].path,
            media_type=res[1].media_type,
            headers={
                "Cache-Control": "private, max-age=31536000, immutable",
                "X-Content-Type-Options": "nosniff",
                "Content-Security-Policy": "sandbox",
                "Content-Encoding": "identity"
            }
        )

ARCHIVE_SELECTION_PARSE_CEILING = 10_000

class ArchiveRequest(BaseModel):
    date_folder: str = Field(pattern=PHOTO_FOLDER_PATTERN.pattern)
    sub_folder: str = Field(default="", max_length=255, pattern="^[^/\\\\]*$")
    selection: List[str] = Field(default_factory=list, max_length=ARCHIVE_SELECTION_PARSE_CEILING)

from ..services.archive_tokens import issue_ticket, inspect_ticket, bind_ticket, ArchiveTicket, archive_attachment_name, Admissible, Expired, Spent, ScopeViolation
from ..services.archive_status import record_status, get_status
from ..services.photo_files import (
    ArchivePermits, ArchiveStreamSession, ArchiveTransport, PermitsExhausted, ReaderBacklog,
    PlanCancelled, PlanDisconnected, PlanFailed, PlanReady, PlanRefused, PlanStalled,
    archive_reader_main, await_plan, emit_archive, finish_response, release,
    return_reader_slot, send_frame, try_admit,
)
import asyncio
import logging
import secrets

import anyio

class ArchiveTokenRead(BaseModel):
    token: str
    filename: str
    expires_in_seconds: float

@router.post("/archive-token")
def create_archive_token(
    req: ArchiveRequest,
    is_loopback: bool = Depends(is_loopback_caller),
    settings: Settings = Depends(get_settings),
):
    if any(len(s) > 255 for s in req.selection):
        return JSONResponse(status_code=422, content={"kind": "invalid_selection"})

    idx = resolve_file_index(req.date_folder, req.sub_folder, settings, time.monotonic)
    if idx.status != PhotoFileListStatus.OK:
        return JSONResponse(status_code=404, content={"kind": idx.status.value})

    seen = set()
    dedup = []
    for s in req.selection:
        if s not in seen:
            seen.add(s)
            dedup.append(s)
    req.selection = dedup

    target_entries = idx.entries if not req.selection else [
        idx.by_name[s] for s in req.selection if s in idx.by_name
    ]

    if len(target_entries) > settings.shipping_photos_max_files_per_folder:
        return JSONResponse(status_code=422, content={"kind": "selection_too_large"})

    if not is_loopback:
        if len(target_entries) > settings.shipping_photos_archive_lan_max_files:
            return JSONResponse(status_code=403,
                                content={"kind": "lan_cap_exceeded", "limit": "files"})
        if sum(e.size_bytes for e in target_entries) > settings.shipping_photos_archive_lan_max_bytes:
            return JSONResponse(status_code=403,
                                content={"kind": "lan_cap_exceeded", "limit": "bytes"})

    filename = archive_attachment_name(req.date_folder, req.sub_folder)
    token = issue_ticket(
        ArchiveTicket(
            date_folder=req.date_folder,
            sub_folder=req.sub_folder,
            selection=list(req.selection),
            filename=filename,
            minted_loopback=is_loopback,
        ),
        settings,
        time.monotonic,
    )

    return ArchiveTokenRead(
        token=token,
        filename=filename,
        expires_in_seconds=settings.shipping_photos_archive_token_ttl_seconds,
    )

def get_archive_permits(request: Request) -> ArchivePermits:
    if not hasattr(request.app.state, "archive_permits"):
        if not hasattr(request.app.state, "_archive_permits_lock"):
            request.app.state._archive_permits_lock = threading.Lock()
        with request.app.state._archive_permits_lock:
            if not hasattr(request.app.state, "archive_permits"):
                settings = get_settings()
                request.app.state.archive_permits = ArchivePermits(
                    capacity=settings.shipping_photos_archive_max_concurrent,
                    reader_ceiling=settings.shipping_photos_archive_max_live_readers
                )
    return request.app.state.archive_permits


class ArchiveStreamingResponse(StreamingResponse):
    """Streams a planned archive under a declared Content-Length.

    Every frame passes the length guard and the send bound; the terminal body
    message is sent only for a whole archive (Phase 32 §3). The iterator is
    closed and the session released on every exit, inside a shield.
    """
    def __init__(self, content, *, session: ArchiveStreamSession, stall_seconds: float, **kwargs):
        super().__init__(content, **kwargs)
        self.session = session
        self.stall_seconds = stall_seconds

    async def stream_response(self, send) -> None:
        started = False
        try:
            async for frame in self.body_iterator:
                if not started:
                    with anyio.move_on_after(self.stall_seconds) as scope:
                        await send({"type": "http.response.start", "status": self.status_code, "headers": self.raw_headers})
                    if scope.cancel_called:
                        release(self.session, "AbandonedDisconnect")
                        return
                    started = True
                if await send_frame(self.session, send, frame, self.stall_seconds) != "Sent":
                    return
            await finish_response(self.session, send, self.stall_seconds)
        finally:
            with anyio.CancelScope(shield=True):
                await self.body_iterator.aclose()
                release(self.session, "AbandonedDisconnect")


@router.get("/archive-status")
def get_archive_status(
    token: str = Query(..., min_length=16, max_length=128),
    is_loopback: bool = Depends(is_loopback_caller),
    settings: Settings = Depends(get_settings)
):
    no_store = {"Cache-Control": "no-store"}
    status = get_status(token, settings, time.monotonic)

    if status:
        if status.minted_loopback and not is_loopback:
            return JSONResponse(status_code=403, content={"kind": "ScopeViolation"}, headers=no_store)
        if status.state == "Terminal":
            return JSONResponse(
                status_code=200,
                content={
                    "state": "Terminal",
                    "outcome": status.outcome,
                    "bytes_sent": status.bytes_sent,
                    "entry_count": status.entry_count,
                    "unresolved_count": status.unresolved_count
                },
                headers=no_store
            )
        return JSONResponse(status_code=200, content={"state": status.state}, headers=no_store)

    inspection = inspect_ticket(token, is_loopback, settings, time.monotonic)
    if isinstance(inspection, ScopeViolation):
        return JSONResponse(status_code=403, content={"kind": "ScopeViolation"}, headers=no_store)
    if isinstance(inspection, Admissible):
        return JSONResponse(status_code=200, content={"state": "Pending"}, headers=no_store)
    return JSONResponse(status_code=200, content={"state": "Unknown"}, headers=no_store)


_rejected_log_times = {}

def _emit_rejected(token: str, reason: str, is_loopback: bool, permits: ArchivePermits, settings: Settings, clock):
    record_status(
        token=token,
        state="Terminal",
        outcome=reason,
        bytes_sent=0,
        entry_count=0,
        unresolved_count=0,
        minted_loopback=is_loopback,
        settings=settings,
        clock=clock
    )

    prefix = token[:8] if token else ""
    key = (prefix, reason)
    now = clock()

    last = _rejected_log_times.get(key, 0)
    if now - last < 2.0:
        return

    _rejected_log_times[key] = now

    if len(_rejected_log_times) > 256:
        stale = [k for k, v in _rejected_log_times.items() if now - v > 60.0]
        for k in stale:
            _rejected_log_times.pop(k, None)

    logging.getLogger("scheduler").warning(
        "ArchiveRejected: token_prefix=%s reason=%s is_loopback=%s permits_in_use=%d live_readers=%d",
        prefix,
        reason,
        is_loopback,
        permits.in_use,
        permits.live_readers
    )


NO_STORE = {"Cache-Control": "no-store"}


@router.get("/archive-download")
async def download_archive(
    request: Request,
    token: str = Query(..., min_length=16, max_length=128),
    is_loopback: bool = Depends(is_loopback_caller),
    settings: Settings = Depends(get_settings),
    permits: ArchivePermits = Depends(get_archive_permits)
):
    inspection = inspect_ticket(token, is_loopback, settings, time.monotonic)
    if isinstance(inspection, Expired):
        _emit_rejected(token, "TokenExpired", is_loopback, permits, settings, time.monotonic)
        return JSONResponse(status_code=404, content={"kind": "token_expired"}, headers=NO_STORE)
    if isinstance(inspection, Spent):
        _emit_rejected(token, "TokenSpent", is_loopback, permits, settings, time.monotonic)
        return JSONResponse(status_code=404, content={"kind": "token_spent"}, headers=NO_STORE)
    if isinstance(inspection, ScopeViolation):
        _emit_rejected(token, "TokenScope", is_loopback, permits, settings, time.monotonic)
        return JSONResponse(status_code=403, content={"kind": "token_scope"}, headers=NO_STORE)
    ticket = inspection.ticket

    try:
        lease = try_admit(permits)
    except PermitsExhausted:
        _emit_rejected(token, "PermitsExhausted", is_loopback, permits, settings, time.monotonic)
        return JSONResponse(status_code=503, content={"kind": "busy"}, headers={"Retry-After": "5", **NO_STORE})
    except ReaderBacklog:
        _emit_rejected(token, "ReaderBacklog", is_loopback, permits, settings, time.monotonic)
        return JSONResponse(status_code=503, content={"kind": "busy"}, headers={"Retry-After": "5", **NO_STORE})

    loop = asyncio.get_running_loop()
    session = ArchiveStreamSession(
        session_id=secrets.token_hex(8),
        transport=ArchiveTransport(settings.shipping_photos_archive_readahead_chunks + 2, loop),
        lease=lease,
        token=token,
        date_folder=ticket.date_folder,
        sub_folder=ticket.sub_folder,
        selection=list(ticket.selection),
        minted_loopback=ticket.minted_loopback,
        settings=settings,
    )
    bound = bind_ticket(token, session.session_id, settings, time.monotonic)
    if isinstance(bound, (Expired, Spent)):
        return_reader_slot(lease)
        release(session, "TicketRefused")
        kind = "token_expired" if isinstance(bound, Expired) else "token_spent"
        return JSONResponse(status_code=404, content={"kind": kind}, headers=NO_STORE)

    record_status(
        token=token,
        state="Preparing",
        outcome=None,
        bytes_sent=0,
        entry_count=0,
        unresolved_count=0,
        minted_loopback=ticket.minted_loopback,
        settings=settings,
        clock=time.monotonic
    )

    session.budget_handle = loop.call_later(
        settings.shipping_photos_archive_session_budget_seconds,
        lambda: release(session, "AbandonedBudget")
    )

    try:
        session.reader = threading.Thread(target=archive_reader_main, args=(session,), daemon=True, name="ArchiveReader")
        session.reader.start()
    except Exception:
        return_reader_slot(lease)
        release(session, "FailedStart")
        return JSONResponse(status_code=500, content={"kind": "thread_start_failed"}, headers=NO_STORE)

    try:
        preflight = await await_plan(session, request.receive, settings.shipping_photos_archive_preflight_stall_seconds)
    except BaseException:
        release(session, "AbandonedDisconnect")
        raise

    if isinstance(preflight, PlanRefused):
        release(session, preflight.reason)
        return JSONResponse(status_code=404, content={"kind": preflight.kind}, headers=NO_STORE)
    if isinstance(preflight, PlanStalled):
        release(session, "PreflightStalled")
        return JSONResponse(status_code=503, content={"kind": "preflight_stalled"}, headers={"Retry-After": "5", **NO_STORE})
    if isinstance(preflight, PlanDisconnected):
        release(session, "AbandonedDisconnect")
        return JSONResponse(status_code=503, content={"kind": "client_disconnected"}, headers=NO_STORE)
    if isinstance(preflight, PlanCancelled):
        return JSONResponse(status_code=503, content={"kind": "budget_exceeded"}, headers=NO_STORE)
    if isinstance(preflight, PlanFailed):
        release(session, "FailedFraming")
        return JSONResponse(status_code=500, content={"kind": "preflight_failed"}, headers=NO_STORE)

    plan = preflight.plan
    session.plan = plan
    record_status(
        token=token,
        state="Streaming",
        outcome=None,
        bytes_sent=0,
        entry_count=plan.file_member_count,
        unresolved_count=len(plan.excluded),
        minted_loopback=ticket.minted_loopback,
        settings=settings,
        clock=time.monotonic
    )

    return ArchiveStreamingResponse(
        emit_archive(session, plan),
        session=session,
        stall_seconds=settings.shipping_photos_archive_send_stall_seconds,
        media_type="application/zip",
        headers={
            "Content-Length": str(plan.declared_bytes),
            "Accept-Ranges": "none",
            "Content-Disposition": f'attachment; filename="{ticket.filename}"',
            "Content-Encoding": "identity",
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )
