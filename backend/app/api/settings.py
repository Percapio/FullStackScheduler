import logging
import os
import threading
from pathlib import Path
from typing import List, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

from ..config import Settings, get_settings
from ..services.runtime_config import effective_photos_dir, save_photos_dir, RuntimeConfigWriteError
from ..services.shipping_photos import PHOTO_FOLDER_PATTERN, invalidate_index
from .deps import is_loopback_caller, require_loopback

logger = logging.getLogger(__name__)

settings_router = APIRouter()

class PhotosDirRead(BaseModel):
    path: Optional[str]
    source: Literal["runtime", "env", "unset"]
    configured: bool
    editable: bool

class BrowseEntry(BaseModel):
    name: str
    path: str

class BrowseRead(BaseModel):
    parent: Optional[str]
    entries: List[BrowseEntry]
    truncated: bool

class PhotosDirWrite(BaseModel):
    path: str

class PhotosDirWriteResponse(PhotosDirRead):
    folder_count: int

_browse_semaphore = None
def _get_browse_semaphore(settings: Settings) -> threading.Semaphore:
    global _browse_semaphore
    if _browse_semaphore is None:
        _browse_semaphore = threading.Semaphore(settings.settings_browse_max_concurrent)
    return _browse_semaphore

@settings_router.get("/photos-dir", response_model=PhotosDirRead)
def get_photos_dir(
    is_loopback: bool = Depends(is_loopback_caller),
    settings: Settings = Depends(get_settings)
):
    dir_path, source = effective_photos_dir(settings)
    configured = bool(dir_path)
    
    return PhotosDirRead(
        path=dir_path if (is_loopback and configured) else None,
        source=source,
        configured=configured,
        editable=is_loopback
    )

@settings_router.get("/browse", response_model=BrowseRead, dependencies=[Depends(require_loopback)])
def browse_directory(
    path: str = Query(default=""),
    prefix: str = Query(default=""),
    settings: Settings = Depends(get_settings)
):
    sem = _get_browse_semaphore(settings)
    if not sem.acquire(blocking=False):
        raise HTTPException(status_code=503, detail={"kind": "busy"})
        
    try:
        if not path:
            # Drive roots
            entries = []
            if os.name == 'nt':
                import ctypes
                bitmask = ctypes.windll.kernel32.GetLogicalDrives()
                for i in range(26):
                    if bitmask & (1 << i):
                        drive = f"{chr(65 + i)}:\\"
                        if not prefix or drive.lower().startswith(prefix.lower()):
                            entries.append(BrowseEntry(name=drive, path=drive))
            else:
                entries.append(BrowseEntry(name="/", path="/"))
            return BrowseRead(parent=None, entries=entries, truncated=False)

        base_path = Path(path)
        if not base_path.exists() or not base_path.is_dir():
            raise HTTPException(status_code=404, detail="Not found or not a directory")

        parent = str(base_path.parent) if base_path.parent != base_path else None
        
        try:
            iterator = os.scandir(base_path)
        except OSError as e:
            logger.warning("Failed to browse %s: %s", path, e)
            raise HTTPException(status_code=404, detail="Not found or not readable")
            
        candidates = []
        truncated = False
        prefix_lower = prefix.lower()
        max_entries = settings.settings_browse_max_entries
        
        with iterator:
            for entry in iterator:
                try:
                    if entry.is_dir():
                        name = entry.name
                        if not prefix_lower or name.lower().startswith(prefix_lower):
                            candidates.append(BrowseEntry(name=name, path=entry.path))
                            # Keep top K by sorting and evicting
                            candidates.sort(key=lambda x: x.name.lower())
                            if len(candidates) > max_entries:
                                candidates.pop()
                                truncated = True
                except OSError:
                    pass

        return BrowseRead(parent=parent, entries=candidates, truncated=truncated)
    finally:
        sem.release()

@settings_router.put("/photos-dir", response_model=PhotosDirWriteResponse, dependencies=[Depends(require_loopback)])
def put_photos_dir(
    payload: PhotosDirWrite,
    request: Request,
    settings: Settings = Depends(get_settings)
):
    candidate = payload.path.strip()
    if not candidate:
        raise HTTPException(status_code=422, detail={"kind": "blank"})
        
    path_obj = Path(candidate)
    if not path_obj.is_absolute():
        raise HTTPException(status_code=422, detail={"kind": "not_absolute"})
        
    if not path_obj.exists():
        raise HTTPException(status_code=422, detail={"kind": "not_found"})
        
    if not path_obj.is_dir():
        raise HTTPException(status_code=422, detail={"kind": "not_a_dir"})
        
    try:
        iterator = os.scandir(path_obj)
    except OSError:
        raise HTTPException(status_code=422, detail={"kind": "not_readable"})
        
    folder_count = 0
    try:
        with iterator:
            try:
                # read one entry to test readability
                first = next(iterator)
                if PHOTO_FOLDER_PATTERN.fullmatch(first.name) and first.is_dir():
                    folder_count += 1
            except StopIteration:
                pass
            
            # read the rest
            for entry in iterator:
                if PHOTO_FOLDER_PATTERN.fullmatch(entry.name) and entry.is_dir():
                    folder_count += 1
    except OSError:
        raise HTTPException(status_code=422, detail={"kind": "not_readable"})

    # Get old path before save for logging
    old_dir, _ = effective_photos_dir(settings)

    try:
        save_photos_dir(str(path_obj))
    except RuntimeConfigWriteError:
        raise HTTPException(status_code=500, detail={"kind": "storage"})
        
    from ..services.photo_files import invalidate_file_index, ALL_FOLDERS
    invalidate_index()
    invalidate_file_index(ALL_FOLDERS())
    
    host = request.client.host if getattr(request, "client", None) else "unknown"
    logger.info("Photos directory changed from %r to %r by %s", old_dir, str(path_obj), host)
    
    # Reload effective to construct response
    new_dir, source = effective_photos_dir(settings)
    
    return PhotosDirWriteResponse(
        path=new_dir,
        source=source,
        configured=bool(new_dir),
        editable=True,
        folder_count=folder_count
    )
