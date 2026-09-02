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

@router.post("/open")
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
