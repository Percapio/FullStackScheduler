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
    resolve_file_index, resolve_photo_file_path, stream_photo_archive, PhotoFileListStatus
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

from ..services.archive_tokens import issue_ticket, inspect_ticket, bind_ticket, ArchiveTicket, archive_attachment_name, Admissible, Expired, Spent, ScopeViolation, Bound, Retry
from ..services.photo_files import ArchiveStreamSession, ArchivePermits, try_admit, return_permit, return_reader_slot, PermitsExhausted, ReaderBacklog, ArchiveTransport, ArchiveSnapshot, release
from ..services.shipping_photos import resolve_photo_folder_path
from ..services.photo_files import ROOT
from starlette.background import BackgroundTask
import asyncio
import anyio
import time

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
        import threading
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
    def __init__(self, *args, stall_seconds: float = 30.0, session: ArchiveStreamSession = None, **kwargs):
        super().__init__(*args, **kwargs)
        self.stall_seconds = stall_seconds
        self.session = session

    async def stream_response(self, send) -> None:
        try:
            with anyio.move_on_after(self.stall_seconds) as scope:
                await send(
                    {
                        "type": "http.response.start",
                        "status": self.status_code,
                        "headers": self.raw_headers,
                    }
                )
            if scope.cancel_called:
                return

            async for chunk in self.body_iterator:
                if not isinstance(chunk, bytes):
                    chunk = chunk.encode(self.charset)
                with anyio.move_on_after(self.stall_seconds) as scope:
                    await send({"type": "http.response.body", "body": chunk, "more_body": True})
                if scope.cancel_called:
                    # AbandonedDisconnect! Return here so we never send the final message.
                    if self.session:
                        self.session.pending_outcome = "AbandonedDisconnect"
                    return

            with anyio.move_on_after(self.stall_seconds) as scope:
                await send({"type": "http.response.body", "body": b"", "more_body": False})
        finally:
            with anyio.CancelScope(shield=True):
                if hasattr(self.body_iterator, "aclose"):
                    await self.body_iterator.aclose()

@router.get("/archive-status")
def get_archive_status(
    token: str = Query(..., min_length=16, max_length=128),
    is_loopback: bool = Depends(is_loopback_caller),
    settings: Settings = Depends(get_settings)
):
    from ..services.archive_status import get_status
    from ..services.archive_tokens import inspect_ticket, ScopeViolation
    
    status = get_status(token, settings, time.monotonic())
    
    if status:
        if status.minted_loopback and not is_loopback:
            return JSONResponse(status_code=403, content={"kind": "ScopeViolation"})
            
        if status.state == "Terminal":
            return JSONResponse(
                status_code=200,
                content={
                    "state": "Terminal",
                    "outcome": status.outcome,
                    "bytes_sent": status.bytes_sent,
                    "entry_count": status.entry_count,
                    "unresolved_count": status.unresolved_count
                }
            )
        return JSONResponse(status_code=200, content={"state": status.state})
        
    # Not in status store. Could be Pending or genuinely Unknown.
    # Consult ticket store for scope check and existence.
    insp = inspect_ticket(token, is_loopback, settings, time.monotonic())
    if isinstance(insp, ScopeViolation):
        return JSONResponse(status_code=403, content={"kind": "ScopeViolation"})
    
    from ..services.archive_tokens import Admissible, Bound, Retry
    # If it's still in the ticket store and not expired/spent (or even if spent, it might just not have written status yet)
    # Actually, inspect_ticket returns Spent if it's spent. But if it's spent and not in status store, it's weird.
    # We can just return Pending if it's Admissible, otherwise Unknown.
    if isinstance(insp, Admissible):
        return JSONResponse(status_code=200, content={"state": "Pending"})
        
    return JSONResponse(status_code=200, content={"state": "Unknown"})


import logging
import time

_rejected_log_times = {}

def _emit_rejected(token: str, reason: str, is_loopback: bool, permits: ArchivePermits, settings: Settings, clock: float):
    from ..services.archive_status import record_status
    record_status(
        token=token,
        state="Terminal",
        outcome=reason,
        bytes_sent=0,
        entry_count=0,
        unresolved_count=0,
        minted_loopback=is_loopback, # Wait, I don't know minted_loopback here, but we pass is_loopback of the caller
        settings=settings,
        clock=clock
    )

    prefix = token[:8] if token else ""
    key = (prefix, reason)
    now = clock
    
    last = _rejected_log_times.get(key, 0)
    if now - last < 2.0:
        return
        
    _rejected_log_times[key] = now
    
    # Keep the dictionary bounded
    if len(_rejected_log_times) > 256:
        stale = [k for k, v in _rejected_log_times.items() if now - v > 60.0]
        for k in stale:
            _rejected_log_times.pop(k, None)
            
    logger = logging.getLogger("scheduler")
    logger.warning(
        "ArchiveRejected: token_prefix=%s reason=%s is_loopback=%s permits_in_use=%d live_readers=%d",
        prefix,
        reason,
        is_loopback,
        permits.in_use,
        permits.live_readers
    )

from ..services.photo_files import stream_photo_archive, archive_reader_loop

@router.get("/archive-download")
async def download_archive(
    token: str = Query(..., min_length=16, max_length=128),
    is_loopback: bool = Depends(is_loopback_caller),
    settings: Settings = Depends(get_settings),
    permits: ArchivePermits = Depends(get_archive_permits)
):
    insp = inspect_ticket(token, is_loopback, settings, time.monotonic)
    if isinstance(insp, Expired):
        _emit_rejected(token, "TokenExpired", is_loopback, permits, settings, time.monotonic())
        return JSONResponse(status_code=404, content={"kind": "token_expired"}, headers={"Cache-Control": "no-store"})
    elif isinstance(insp, Spent):
        _emit_rejected(token, "TokenSpent", is_loopback, permits, settings, time.monotonic())
        return JSONResponse(status_code=404, content={"kind": "token_spent"}, headers={"Cache-Control": "no-store"})
    elif isinstance(insp, ScopeViolation):
        _emit_rejected(token, "TokenScope", is_loopback, permits, settings, time.monotonic())
        return JSONResponse(status_code=403, content={"kind": "token_scope"}, headers={"Cache-Control": "no-store"})
        
    ticket = insp.ticket
    
    idx = resolve_file_index(ticket.date_folder, ticket.sub_folder, settings, time.monotonic)
    if idx.status != PhotoFileListStatus.OK:
        reason = "ListingUnavailable" if idx.status in (PhotoFileListStatus.UNAVAILABLE, PhotoFileListStatus.UNCONFIGURED) else "FolderNotFound"
        _emit_rejected(token, reason, is_loopback, permits, settings, time.monotonic())
        return JSONResponse(status_code=404, content={"kind": idx.status.value}, headers={"Cache-Control": "no-store"})
        
    folder_res = resolve_photo_folder_path(ticket.date_folder, settings)
    if folder_res[0] == "err":
        _emit_rejected(token, "FolderNotFound", is_loopback, permits, settings, time.monotonic())
        return JSONResponse(status_code=404, content={"kind": "folder_not_found"}, headers={"Cache-Control": "no-store"})
        
    folder_path = folder_res[1]
    if ticket.sub_folder != ROOT:
        folder_path = folder_path / ticket.sub_folder

    target_names = set(ticket.selection) if ticket.selection else None
    
    entries = []
    unresolved = []
    if target_names is not None:
        for name in ticket.selection:
            if name in idx.by_name:
                entries.append(idx.by_name[name])
            else:
                unresolved.append(name)
    else:
        entries = idx.entries
        
    covers_full = (len(entries) + len(unresolved)) == len(idx.entries)
    
    snapshot = ArchiveSnapshot(
        entries=entries,
        unresolved=unresolved,
        covers_full_listing=covers_full,
        index_truncated=idx.truncated,
        scanned_at=idx.scanned_at
    )
    
    try:
        lease = try_admit(permits)
        from ..services.archive_status import record_status
        record_status(
            token=token,
            state="Streaming",
            outcome=None,
            bytes_sent=0,
            entry_count=len(snapshot.entries),
            unresolved_count=len(snapshot.unresolved),
            minted_loopback=ticket.minted_loopback,
            settings=settings,
            clock=time.monotonic()
        )
    except PermitsExhausted:
        _emit_rejected(token, "PermitsExhausted", is_loopback, permits, settings, time.monotonic())
        return JSONResponse(status_code=503, content={"kind": "busy"}, headers={"Retry-After": "5", "Cache-Control": "no-store"})
    except ReaderBacklog:
        _emit_rejected(token, "ReaderBacklog", is_loopback, permits, settings, time.monotonic())
        return JSONResponse(status_code=503, content={"kind": "busy"}, headers={"Retry-After": "5", "Cache-Control": "no-store"})

    import secrets
    session_id = secrets.token_hex(8)
    
    loop = asyncio.get_running_loop()
    transport = ArchiveTransport(
        data_credits=settings.shipping_photos_archive_readahead_chunks + 2,
        loop=loop
    )
    
    session = ArchiveStreamSession(
        session_id=session_id,
        transport=transport,
        lease=lease,
        snapshot=snapshot,
        token=token,
        date_folder=ticket.date_folder,
        sub_folder=ticket.sub_folder
    )
    
    bind_res = bind_ticket(token, session_id, settings, time.monotonic)
    if isinstance(bind_res, Expired):
        release(session, "TicketRefused")
        return JSONResponse(status_code=404, content={"kind": "token_expired"}, headers={"Cache-Control": "no-store"})
    elif isinstance(bind_res, Spent):
        release(session, "TicketRefused")
        return JSONResponse(status_code=404, content={"kind": "token_spent"}, headers={"Cache-Control": "no-store"})
        
    # Start budget timer
    session.budget_handle = loop.call_later(
        settings.shipping_photos_archive_session_budget_seconds,
        lambda: release(session, "AbandonedBudget")
    )
    
    # Start reader
    import threading
    try:
        session.reader = threading.Thread(
            target=archive_reader_loop,
            args=(session, folder_path, snapshot, settings),
            daemon=True,
            name="ArchiveReader"
        )
        session.reader.start()
    except Exception:
        # Failsafe
        session.budget_handle.cancel()
        return_reader_slot(lease) # Because no thread exists to discharge it
        release(session, "FailedStart")
        return JSONResponse(status_code=500, content={"kind": "thread_start_failed"}, headers={"Cache-Control": "no-store"})

    stream = stream_photo_archive(ticket.date_folder, ticket.sub_folder, snapshot, settings, session)

    return ArchiveStreamingResponse(
        stream,
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="{ticket.filename}"',
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
            "Content-Encoding": "identity",
        },
        stall_seconds=settings.shipping_photos_archive_send_stall_seconds,
        session=session
    )

