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

from ..services.archive_tokens import issue_ticket, redeem_ticket, ArchiveTicket, archive_attachment_name

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
    # Verify individual item length
    if any(len(s) > 255 for s in req.selection):
        return JSONResponse(status_code=422, content={"kind": "invalid_selection"})

    idx = resolve_file_index(req.date_folder, req.sub_folder, settings, time.monotonic)
    if idx.status != PhotoFileListStatus.OK:
        return JSONResponse(status_code=404, content={"kind": idx.status.value})

    # Deduplicate while preserving order
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

    sem = get_archive_semaphore(settings)
    if not sem.acquire(blocking=False):
        return JSONResponse(status_code=503, content={"kind": "busy"},
                            headers={"Retry-After": "5"})
    sem.release()
    
    covers_full_listing = (len(target_entries) == len(idx.entries))

    filename = archive_attachment_name(req.date_folder, req.sub_folder)
    token = issue_ticket(
        ArchiveTicket(
            date_folder=req.date_folder,
            sub_folder=req.sub_folder,
            selection=list(req.selection),
            filename=filename,
            minted_loopback=is_loopback,
            covers_full_listing=covers_full_listing,
        ),
        settings,
        time.monotonic,
    )
    
    return ArchiveTokenRead(
        token=token,
        filename=filename,
        expires_in_seconds=settings.shipping_photos_archive_token_ttl_seconds,
    )

from ..services.photo_files import ArchiveStreamSession
from ..services.shipping_photos import resolve_photo_folder_path
from ..services.photo_files import ROOT

@router.get("/archive-download")
def download_archive(
    token: str = Query(..., min_length=16, max_length=128),
    is_loopback: bool = Depends(is_loopback_caller),
    settings: Settings = Depends(get_settings),
):
    ticket = redeem_ticket(token, settings, time.monotonic)
    if ticket is None:
        return JSONResponse(status_code=404, content={"kind": "token_expired"},
                            headers={"Cache-Control": "no-store"})

    if ticket.minted_loopback and not is_loopback:
        return JSONResponse(status_code=403, content={"kind": "token_scope"},
                            headers={"Cache-Control": "no-store"})

    idx = resolve_file_index(ticket.date_folder, ticket.sub_folder, settings, time.monotonic)
    if idx.status != PhotoFileListStatus.OK:
        return JSONResponse(status_code=404, content={"kind": idx.status.value},
                            headers={"Cache-Control": "no-store"})

    sem = get_archive_semaphore(settings)
    if not sem.acquire(blocking=False):
        return JSONResponse(status_code=503, content={"kind": "busy"},
                            headers={"Retry-After": "5", "Cache-Control": "no-store"})

    session = ArchiveStreamSession(sem, settings)
    
    folder_res = resolve_photo_folder_path(ticket.date_folder, settings)
    if folder_res[0] == "err":
        session.release()
        return JSONResponse(status_code=404, content={"kind": "folder_not_found"},
                            headers={"Cache-Control": "no-store"})
    
    folder_path = folder_res[1]
    if ticket.sub_folder != ROOT:
        folder_path = folder_path / ticket.sub_folder
        
    target_entries = idx.entries if not ticket.selection else [idx.by_name[s] for s in ticket.selection if s in idx.by_name]
    
    try:
        session.start(folder_path, target_entries, settings)
    except Exception:
        return JSONResponse(status_code=500, content={"kind": "thread_start_failed"},
                            headers={"Cache-Control": "no-store"})

    stream = stream_photo_archive(ticket.date_folder, ticket.sub_folder, ticket.selection, idx, settings, session, covers_full_listing=ticket.covers_full_listing)

    return StreamingResponse(
        stream,
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="{ticket.filename}"',
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
            "Content-Encoding": "identity",
        },
    )


_archive_semaphore = None
def get_archive_semaphore(settings: Settings):
    global _archive_semaphore
    if _archive_semaphore is None:
        _archive_semaphore = threading.Semaphore(settings.shipping_photos_archive_max_concurrent)
    return _archive_semaphore
