import re
from pathlib import Path

path = Path(r"d:\Dev\Scheduler\Schedule\backend\app\api\photos.py")
content = path.read_text(encoding="utf-8")

idx = content.find("ARCHIVE_SELECTION_PARSE_CEILING = 10_000")
prefix = content[:idx]

new_content = prefix + """ARCHIVE_SELECTION_PARSE_CEILING = 10_000

class ArchiveRequest(BaseModel):
    date_folder: str = Field(pattern=PHOTO_FOLDER_PATTERN.pattern)
    sub_folder: str = Field(default="", max_length=255, pattern="^[^/\\\\\\\\]*$")
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
    async def stream_response(self, send) -> None:
        try:
            with anyio.CancelScope(shield=True):
                await super().stream_response(send)
        finally:
            if hasattr(self.body_iterator, "aclose"):
                await self.body_iterator.aclose()

import logging
def _emit_rejected(token: str, reason: str, is_loopback: bool, permits: ArchivePermits):
    logger = logging.getLogger("scheduler")
    logger.warning(
        "ArchiveRejected: token_prefix=%s reason=%s is_loopback=%s permits_in_use=%d live_readers=%d",
        token[:8] if token else "",
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
        _emit_rejected(token, "TokenExpired", is_loopback, permits)
        return JSONResponse(status_code=404, content={"kind": "token_expired"}, headers={"Cache-Control": "no-store"})
    elif isinstance(insp, Spent):
        _emit_rejected(token, "TokenSpent", is_loopback, permits)
        return JSONResponse(status_code=404, content={"kind": "token_spent"}, headers={"Cache-Control": "no-store"})
    elif isinstance(insp, ScopeViolation):
        _emit_rejected(token, "TokenScope", is_loopback, permits)
        return JSONResponse(status_code=403, content={"kind": "token_scope"}, headers={"Cache-Control": "no-store"})
        
    ticket = insp.ticket
    
    idx = resolve_file_index(ticket.date_folder, ticket.sub_folder, settings, time.monotonic)
    if idx.status != PhotoFileListStatus.OK:
        reason = "ListingUnavailable" if idx.status in (PhotoFileListStatus.UNAVAILABLE, PhotoFileListStatus.UNCONFIGURED) else "FolderNotFound"
        _emit_rejected(token, reason, is_loopback, permits)
        return JSONResponse(status_code=404, content={"kind": idx.status.value}, headers={"Cache-Control": "no-store"})
        
    folder_res = resolve_photo_folder_path(ticket.date_folder, settings)
    if folder_res[0] == "err":
        _emit_rejected(token, "FolderNotFound", is_loopback, permits)
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
    except PermitsExhausted:
        _emit_rejected(token, "PermitsExhausted", is_loopback, permits)
        return JSONResponse(status_code=503, content={"kind": "busy"}, headers={"Retry-After": "5", "Cache-Control": "no-store"})
    except ReaderBacklog:
        _emit_rejected(token, "ReaderBacklog", is_loopback, permits)
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
    )

"""

path.write_text(new_content, encoding="utf-8")
print("Done")
