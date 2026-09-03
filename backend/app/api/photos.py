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
    entries: List[PhotoFileEntryRead]
    truncated: bool

@router.get("/files", response_model=PhotoFileListRead)
def list_files(
    date_folder: str = Query(..., pattern=PHOTO_FOLDER_PATTERN.pattern),
    settings: Settings = Depends(get_settings)
):
    idx = resolve_file_index(date_folder, settings, time.monotonic)
    try:
        from ..services.photo_warm import enqueue_warm
        enqueue_warm(date_folder, settings)
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
        entries=entries,
        truncated=idx.truncated
    )

@router.get("/file/{filename}")
def get_file(
    filename: str,
    date_folder: str = Query(..., pattern=PHOTO_FOLDER_PATTERN.pattern),
    settings: Settings = Depends(get_settings)
):
    idx = resolve_file_index(date_folder, settings, time.monotonic)
    res = resolve_photo_file_path(date_folder, filename, idx, settings)
    
    if res[0] == "err":
        return JSONResponse(status_code=404, content={"kind": res[1]})
        
    return FileResponse(
        res[1],
        headers={
            "Cache-Control": "private, max-age=31536000, immutable",
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": "sandbox"
        },
    )


from ..services.photo_thumbnails import generate_once

@router.get("/thumb/{filename}")
def get_thumb(
    filename: str,
    date_folder: str = Query(..., pattern=PHOTO_FOLDER_PATTERN.pattern),
    settings: Settings = Depends(get_settings)
):
    idx = resolve_file_index(date_folder, settings, time.monotonic)
    
    res = generate_once(date_folder, filename, idx, "interactive", settings)
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
                "Content-Security-Policy": "sandbox"
            }
        )

class ArchiveRequest(BaseModel):
    date_folder: str = Field(pattern=PHOTO_FOLDER_PATTERN.pattern)
    selection: List[str] = Field(default_factory=list)

_archive_semaphore = None
def get_archive_semaphore(settings: Settings):
    global _archive_semaphore
    if _archive_semaphore is None:
        _archive_semaphore = threading.Semaphore(settings.shipping_photos_archive_max_concurrent)
    return _archive_semaphore

def hold_permit_across_stream(iterator, sem):
    try:
        yield from iterator
    finally:
        sem.release()

@router.post("/archive")
def create_archive(
    req: ArchiveRequest,
    is_loopback: bool = Depends(is_loopback_caller),
    settings: Settings = Depends(get_settings)
):
    idx = resolve_file_index(req.date_folder, settings, time.monotonic)
    if idx.status != PhotoFileListStatus.OK:
        return JSONResponse(status_code=404, content={"kind": idx.status.value})
        
    if not is_loopback:
        target_entries = idx.entries if not req.selection else [idx.by_name[s] for s in req.selection if s in idx.by_name]
        total_files = len(target_entries)
        total_bytes = sum(e.size_bytes for e in target_entries)
        
        if total_files > settings.shipping_photos_archive_lan_max_files:
            return JSONResponse(status_code=403, content={"kind": "lan_cap_exceeded", "limit": "files"})
        if total_bytes > settings.shipping_photos_archive_lan_max_bytes:
            return JSONResponse(status_code=403, content={"kind": "lan_cap_exceeded", "limit": "bytes"})
            
    sem = get_archive_semaphore(settings)
    if not sem.acquire(blocking=False):
        return JSONResponse(status_code=503, content={"kind": "busy"}, headers={"Retry-After": "5"})
        
    stream = stream_photo_archive(req.date_folder, req.selection, idx, settings)
    
    return StreamingResponse(
        hold_permit_across_stream(stream, sem),
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="Photos_{req.date_folder}.zip"'
        }
    )
