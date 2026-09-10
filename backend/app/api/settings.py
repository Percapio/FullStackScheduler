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

    # Containment check against auto-copy source
    from ..services.runtime_config import load_runtime_config
    config = load_runtime_config()
    auto_copy_source = config.get("shipping_photos_auto_copy_source")
    if auto_copy_source:
        try:
            resolved_dest = path_obj.resolve()
            resolved_src = Path(auto_copy_source).resolve()
            try:
                resolved_dest.relative_to(resolved_src)
                raise HTTPException(status_code=422, detail={"kind": "destination_inside_source"})
            except ValueError:
                try:
                    resolved_src.relative_to(resolved_dest)
                    raise HTTPException(status_code=422, detail={"kind": "source_inside_destination"})
                except ValueError:
                    pass
        except OSError:
            pass # ignore resolution errors during settings update

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

from ..services.photo_sync import get_photo_sync_state, get_worker_running, _run_photo_sync_locked
import time

class AutoCopyRead(BaseModel):
    enabled: bool
    source: Optional[str]
    source_configured: bool
    scheduled_time: Optional[str]
    editable: bool
    running: bool
    run_started_at: Optional[str]
    last_run_finished_at: Optional[str]
    last_run_outcome: Optional[str]
    last_run_files_copied: int
    last_run_files_failed: int
    last_run_dates_skipped: List[str]
    last_completed_date: Optional[str]
    last_error_kind: str

@settings_router.get("/auto-copy", response_model=AutoCopyRead)
def get_auto_copy(
    is_loopback: bool = Depends(is_loopback_caller)
):
    from ..services.runtime_config import load_runtime_config
    config = load_runtime_config()
    state = get_photo_sync_state()
    
    return AutoCopyRead(
        enabled=bool(config.get("shipping_photos_auto_copy_enabled")),
        source=config.get("shipping_photos_auto_copy_source") if is_loopback else None,
        source_configured=bool(config.get("shipping_photos_auto_copy_source")),
        scheduled_time=config.get("shipping_photos_auto_copy_time"),
        editable=is_loopback,
        running=get_worker_running(),
        run_started_at=state.get("last_run_started_at"),
        last_run_finished_at=state.get("last_run_finished_at"),
        last_run_outcome=state.get("last_run_outcome"),
        last_run_files_copied=state.get("last_run_files_copied", 0),
        last_run_files_failed=state.get("last_run_files_failed", 0),
        last_run_dates_skipped=state.get("last_run_dates_skipped", []),
        last_completed_date=state.get("last_completed_date"),
        last_error_kind=state.get("last_error_kind", "None")
    )

class AutoCopyWrite(BaseModel):
    enabled: bool
    source: str
    scheduled_time: str

@settings_router.put("/auto-copy", response_model=AutoCopyRead, dependencies=[Depends(require_loopback)])
def put_auto_copy(
    payload: AutoCopyWrite,
    settings: Settings = Depends(get_settings)
):
    import re
    if not re.match(r"^([01]\d|2[0-3]):[0-5]\d$", payload.scheduled_time):
        raise HTTPException(status_code=422, detail={"kind": "bad_time"})
        
    source_candidate = payload.source.strip()
    if not source_candidate and payload.enabled:
        raise HTTPException(status_code=422, detail={"kind": "no_source"})
        
    if source_candidate:
        source_obj = Path(source_candidate)
        if not source_obj.is_absolute():
            raise HTTPException(status_code=422, detail={"kind": "not_absolute"})
        if not source_obj.exists():
            raise HTTPException(status_code=422, detail={"kind": "not_found"})
        if not source_obj.is_dir():
            raise HTTPException(status_code=422, detail={"kind": "not_a_dir"})
        try:
            os.scandir(source_obj).close()
        except OSError:
            raise HTTPException(status_code=422, detail={"kind": "not_readable"})
            
        dest_dir, _ = effective_photos_dir(settings)
        if dest_dir:
            dest_obj = Path(dest_dir)
            try:
                resolved_src = source_obj.resolve()
                resolved_dest = dest_obj.resolve()
                if resolved_src == resolved_dest:
                    raise HTTPException(status_code=422, detail={"kind": "source_is_destination"})
                try:
                    resolved_dest.relative_to(resolved_src)
                    raise HTTPException(status_code=422, detail={"kind": "destination_inside_source"})
                except ValueError:
                    pass
                try:
                    resolved_src.relative_to(resolved_dest)
                    raise HTTPException(status_code=422, detail={"kind": "source_inside_destination"})
                except ValueError:
                    pass
            except OSError:
                pass
                
    from ..services.runtime_config import save_runtime_config
    try:
        from datetime import datetime
        save_runtime_config({
            "shipping_photos_auto_copy_enabled": payload.enabled,
            "shipping_photos_auto_copy_source": source_candidate,
            "shipping_photos_auto_copy_time": payload.scheduled_time
        }, datetime.now)
    except Exception:
        raise HTTPException(status_code=500, detail={"kind": "storage"})
        
    return get_auto_copy(is_loopback=True)

@settings_router.post("/auto-copy/run", dependencies=[Depends(require_loopback)])
def run_auto_copy(settings: Settings = Depends(get_settings)):
    from ..services.runtime_config import load_runtime_config
    config = load_runtime_config()
    if not config.get("shipping_photos_auto_copy_enabled"):
        raise HTTPException(status_code=409, detail={"kind": "disabled"})
        
    import threading
    dummy_event = threading.Event()
    
    # We run it in a background thread using the locking helper
    def background_run():
        import time
        _run_photo_sync_locked(settings, dummy_event, time.monotonic)
        
    # Attempt to start. We can check if it's already running first to return 409 immediately.
    if get_worker_running():
        raise HTTPException(status_code=409, detail={"kind": "already_running"})
        
    t = threading.Thread(target=background_run, daemon=True, name="ManualPhotoSync")
    t.start()
    
    # Allow thread to start and grab lock
    time.sleep(0.01) 
    
    if not get_worker_running():
        # Might have failed immediately or lock wasn't grabbed (should not happen if we got here)
        pass
        
    from fastapi.responses import JSONResponse
    return JSONResponse(status_code=202, content={})
