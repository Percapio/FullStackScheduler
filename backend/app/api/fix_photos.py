import re

with open("photos.py", "r", encoding="utf-8") as f:
    text = f.read()

# Fix create_archive_token
fixed = re.sub(
    r"    if len\(target_entries\) > settings.shipping_photos_max_files_per_folder:(.*?)    if ticket.minted_loopback",
    r"""    if len(target_entries) > settings.shipping_photos_max_files_per_folder:
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
from ..services.shipping_photos import resolve_photo_folder_path, ROOT

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

    if ticket.minted_loopback""",
    text,
    flags=re.DOTALL
)

with open("photos.py", "w", encoding="utf-8") as f:
    f.write(fixed)
