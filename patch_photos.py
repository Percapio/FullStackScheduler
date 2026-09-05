import re

with open("backend/app/api/photos.py", "r") as f:
    content = f.read()

# Replace PhotoFileListRead
content = content.replace(
    """class PhotoFileListRead(BaseModel):
    status: Literal["unconfigured", "unavailable", "not_found", "ok"]
    entries: List[PhotoFileEntryRead]
    truncated: bool""",
    """class PhotoFileListRead(BaseModel):
    status: Literal["unconfigured", "unavailable", "not_found", "ok"]
    folders: List[str]
    entries: List[PhotoFileEntryRead]
    truncated: bool
    folders_truncated: bool"""
)

# /files
content = content.replace(
    """def list_files(
    date_folder: str = Query(..., pattern=PHOTO_FOLDER_PATTERN.pattern),
    settings: Settings = Depends(get_settings)
):
    idx = resolve_file_index(date_folder, settings, time.monotonic)
    try:
        from ..services.photo_warm import enqueue_warm
        enqueue_warm(date_folder, settings)""",
    """def list_files(
    date_folder: str = Query(..., pattern=PHOTO_FOLDER_PATTERN.pattern),
    sub_folder: str = Query(default="", max_length=255, pattern="^[^/\\\\\\\\]*$"),
    settings: Settings = Depends(get_settings)
):
    idx = resolve_file_index(date_folder, sub_folder, settings, time.monotonic)
    try:
        from ..services.photo_warm import enqueue_warm
        enqueue_warm(date_folder, sub_folder, settings)"""
)

content = content.replace(
    """return PhotoFileListRead(
        status=idx.status.value,
        entries=entries,
        truncated=idx.truncated
    )""",
    """return PhotoFileListRead(
        status=idx.status.value,
        folders=idx.folders if idx.status == PhotoFileListStatus.OK else [],
        entries=entries,
        truncated=idx.truncated,
        folders_truncated=idx.folders_truncated if idx.status == PhotoFileListStatus.OK else False
    )"""
)

# /file/{filename}
content = content.replace(
    """def get_file(
    filename: str,
    date_folder: str = Query(..., pattern=PHOTO_FOLDER_PATTERN.pattern),
    settings: Settings = Depends(get_settings)
):
    idx = resolve_file_index(date_folder, settings, time.monotonic)
    res = resolve_photo_file_path(date_folder, filename, idx, settings)""",
    """def get_file(
    filename: str,
    date_folder: str = Query(..., pattern=PHOTO_FOLDER_PATTERN.pattern),
    sub_folder: str = Query(default="", max_length=255, pattern="^[^/\\\\\\\\]*$"),
    settings: Settings = Depends(get_settings)
):
    idx = resolve_file_index(date_folder, sub_folder, settings, time.monotonic)
    res = resolve_photo_file_path(date_folder, sub_folder, filename, idx, settings)"""
)

# /thumb/{filename}
content = content.replace(
    """def get_thumb(
    filename: str,
    date_folder: str = Query(..., pattern=PHOTO_FOLDER_PATTERN.pattern),
    settings: Settings = Depends(get_settings)
):
    idx = resolve_file_index(date_folder, settings, time.monotonic)
    
    res = generate_once(date_folder, filename, idx, "interactive", settings)""",
    """def get_thumb(
    filename: str,
    date_folder: str = Query(..., pattern=PHOTO_FOLDER_PATTERN.pattern),
    sub_folder: str = Query(default="", max_length=255, pattern="^[^/\\\\\\\\]*$"),
    settings: Settings = Depends(get_settings)
):
    idx = resolve_file_index(date_folder, sub_folder, settings, time.monotonic)
    
    res = generate_once(date_folder, sub_folder, filename, idx, "interactive", settings)"""
)

# ArchiveRequest
content = content.replace(
    """class ArchiveRequest(BaseModel):
    date_folder: str = Field(pattern=PHOTO_FOLDER_PATTERN.pattern)
    selection: List[str] = Field(default_factory=list)""",
    """class ArchiveRequest(BaseModel):
    date_folder: str = Field(pattern=PHOTO_FOLDER_PATTERN.pattern)
    sub_folder: str = Field(default="", max_length=255, pattern="^[^/\\\\\\\\]*$")
    selection: List[str] = Field(default_factory=list)"""
)

# /archive-token imports
content = content.replace(
    """from ..services.archive_tokens import issue_ticket, redeem_ticket, ArchiveTicket""",
    """from ..services.archive_tokens import issue_ticket, redeem_ticket, ArchiveTicket, archive_attachment_name"""
)

# /archive-token body
content = content.replace(
    """    idx = resolve_file_index(req.date_folder, settings, time.monotonic)""",
    """    idx = resolve_file_index(req.date_folder, req.sub_folder, settings, time.monotonic)"""
)

content = content.replace(
    """    filename = f"Photos_{req.date_folder}.zip"
    token = issue_ticket(
        ArchiveTicket(
            date_folder=req.date_folder,
            selection=list(req.selection),
            filename=filename,
            minted_loopback=is_loopback,
        ),""",
    """    filename = archive_attachment_name(req.date_folder, req.sub_folder)
    token = issue_ticket(
        ArchiveTicket(
            date_folder=req.date_folder,
            sub_folder=req.sub_folder,
            selection=list(req.selection),
            filename=filename,
            minted_loopback=is_loopback,
        ),"""
)

# /archive-download body
content = content.replace(
    """    idx = resolve_file_index(ticket.date_folder, settings, time.monotonic)""",
    """    idx = resolve_file_index(ticket.date_folder, ticket.sub_folder, settings, time.monotonic)"""
)

content = content.replace(
    """    stream = stream_photo_archive(ticket.date_folder, ticket.selection, idx, settings)""",
    """    stream = stream_photo_archive(ticket.date_folder, ticket.sub_folder, ticket.selection, idx, settings)"""
)

# /archive body
content = content.replace(
    """    stream = stream_photo_archive(req.date_folder, req.selection, idx, settings)
    
    return StreamingResponse(
        hold_permit_across_stream(stream, sem),
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="Photos_{req.date_folder}.zip"'
        }
    )""",
    """    stream = stream_photo_archive(req.date_folder, req.sub_folder, req.selection, idx, settings)
    
    return StreamingResponse(
        hold_permit_across_stream(stream, sem),
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="{archive_attachment_name(req.date_folder, req.sub_folder)}"'
        }
    )"""
)

with open("backend/app/api/photos.py", "w") as f:
    f.write(content)
