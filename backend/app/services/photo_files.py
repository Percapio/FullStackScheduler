import os
import re
import stat
import time
import zipfile
from collections import OrderedDict
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from threading import Lock
from typing import Callable, Iterator, List, Dict, Literal, Union, Optional

from ..config import Settings
from .shipping_photos import resolve_photo_folder_path, is_photo_folder_name

FileName = str
VersionTag = str
Extension = str
MediaType = str

@dataclass
class FileStatus:
    is_regular_file: bool
    size_bytes: int
    mtime_ns: int

class PhotoFileListStatus(str, Enum):
    UNCONFIGURED = "unconfigured"
    UNAVAILABLE = "unavailable"
    NOT_FOUND = "not_found"
    OK = "ok"

@dataclass
class PhotoFileEntry:
    name: FileName
    size_bytes: int
    mtime_ns: int
    version: VersionTag
    previewable: bool

@dataclass
class PhotoFileIndex:
    status: PhotoFileListStatus
    entries: List[PhotoFileEntry]
    by_name: Dict[FileName, PhotoFileEntry]
    total_bytes: int
    scanned_at: float
    truncated: bool

PhotoFileFailure = Literal[
    "unconfigured",
    "unavailable",
    "folder_not_found",
    "file_not_found",
    "not_a_file"
]

FILE_NAME_PREFILTER = re.compile("^[^\\x00-\\x1f<>:\"/\\\\|?*]{1,255}$")

def is_plausible_file_name(candidate: str) -> bool:
    if not candidate:
        return False
    return bool(FILE_NAME_PREFILTER.match(candidate))

def resolve_photo_file_path(
    date_folder: str,
    file_name: FileName,
    index: PhotoFileIndex,
    settings: Settings,
    resolve: Callable[[Path], Path] = lambda p: p.resolve(),
    stat_fn: Callable[[Path], FileStatus] = lambda p: _default_stat(p)
) -> Union[tuple[Literal["ok"], Path], tuple[Literal["err"], PhotoFileFailure]]:
    
    # 1. Resolve folder
    folder_res = resolve_photo_folder_path(date_folder, settings)
    if folder_res[0] == "err":
        if folder_res[1] == "unconfigured":
            return "err", "unconfigured"
        elif folder_res[1] == "unavailable":
            return "err", "unavailable"
        else: # invalid_name or not_found
            return "err", "folder_not_found"
            
    resolved_folder = folder_res[1]
    
    # 2. Pre-filter
    if not is_plausible_file_name(file_name):
        return "err", "file_not_found"
        
    # 3. Membership
    if file_name not in index.by_name:
        return "err", "file_not_found"
        
    # 4. Join + Fully Resolve both
    joined = resolved_folder / file_name
    try:
        resolved_file = resolve(joined)
        resolved_folder_abs = resolve(resolved_folder)
        # Check containment
        resolved_file.relative_to(resolved_folder_abs)
    except (OSError, ValueError):
        return "err", "not_a_file"
        
    # 5. Regular file check
    try:
        f_stat = stat_fn(resolved_file)
        if not f_stat.is_regular_file:
            return "err", "not_a_file"
    except OSError:
        return "err", "file_not_found"
        
    return "ok", resolved_file

def _default_stat(p: Path) -> FileStatus:
    st = p.stat()
    import stat as st_module
    return FileStatus(
        is_regular_file=st_module.S_ISREG(st.st_mode),
        size_bytes=st.st_size,
        mtime_ns=st.st_mtime_ns
    )

_file_index_lock = Lock()
_file_indexes: OrderedDict[str, PhotoFileIndex] = OrderedDict()

PREVIEWABLE_EXTENSIONS: Dict[Extension, MediaType] = {
    ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
    ".png": "image/png",  ".webp": "image/webp",
    ".gif": "image/gif"
}

def resolve_file_index(
    date_folder: str,
    settings: Settings,
    clock: Callable[[], float]
) -> PhotoFileIndex:
    now = clock()
    
    with _file_index_lock:
        if date_folder in _file_indexes:
            cached = _file_indexes[date_folder]
            age = now - cached.scanned_at
            
            if cached.status == PhotoFileListStatus.OK:
                if age < settings.shipping_photos_file_index_ttl_seconds:
                    _file_indexes.move_to_end(date_folder)
                    return cached
            else:
                if age < settings.shipping_photos_file_unavailable_ttl_seconds:
                    _file_indexes.move_to_end(date_folder)
                    return cached

        # Scan
        folder_res = resolve_photo_folder_path(date_folder, settings)
        if folder_res[0] == "err":
            status = PhotoFileListStatus.NOT_FOUND
            if folder_res[1] == "unconfigured":
                status = PhotoFileListStatus.UNCONFIGURED
            elif folder_res[1] == "unavailable":
                status = PhotoFileListStatus.UNAVAILABLE
            
            idx = PhotoFileIndex(
                status=status,
                entries=[],
                by_name={},
                total_bytes=0,
                scanned_at=now,
                truncated=False
            )
        else:
            folder_path = folder_res[1]
            try:
                iterator = os.scandir(folder_path)
            except OSError:
                idx = PhotoFileIndex(
                    status=PhotoFileListStatus.NOT_FOUND,
                    entries=[],
                    by_name={},
                    total_bytes=0,
                    scanned_at=now,
                    truncated=False
                )
            else:
                entries = []
                total_bytes = 0
                truncated = False
                
                with iterator:
                    for entry in iterator:
                        try:
                            if entry.is_file(): # which checks is_regular_file
                                stat_res = entry.stat()
                                size = stat_res.st_size
                                mtime = stat_res.st_mtime_ns
                                ext = Path(entry.name).suffix.lower()
                                
                                entries.append(PhotoFileEntry(
                                    name=entry.name,
                                    size_bytes=size,
                                    mtime_ns=mtime,
                                    version=f"{mtime}-{size}",
                                    previewable=ext in PREVIEWABLE_EXTENSIONS
                                ))
                        except OSError:
                            pass
                
                # ASCII-friendly lexicographical sort
                entries.sort(key=lambda e: e.name)
                
                if len(entries) > settings.shipping_photos_max_files_per_folder:
                    entries = entries[:settings.shipping_photos_max_files_per_folder]
                    truncated = True
                
                for e in entries:
                    total_bytes += e.size_bytes
                    
                by_name = {e.name: e for e in entries}
                
                idx = PhotoFileIndex(
                    status=PhotoFileListStatus.OK,
                    entries=entries,
                    by_name=by_name,
                    total_bytes=total_bytes,
                    scanned_at=now,
                    truncated=truncated
                )
        
        _file_indexes[date_folder] = idx
        _file_indexes.move_to_end(date_folder)
        
        if len(_file_indexes) > settings.shipping_photos_file_index_max_folders:
            _file_indexes.popitem(last=False)
            
        return idx

class ALL_FOLDERS:
    pass

def invalidate_file_index(target: Union[str, ALL_FOLDERS]) -> None:
    with _file_index_lock:
        if isinstance(target, ALL_FOLDERS):
            _file_indexes.clear()
        else:
            _file_indexes.pop(target, None)

import logging
logger = logging.getLogger(__name__)

def stream_photo_archive(
    date_folder: str,
    selection: List[FileName],
    index: PhotoFileIndex,
    settings: Settings
) -> Iterator[bytes]:
    
    class FileLikeGenerator:
        def __init__(self):
            self.chunks = []
            self.offset = 0
        def write(self, data: bytes):
            self.chunks.append(data)
            self.offset += len(data)
            return len(data)
        def tell(self):
            return self.offset
        def flush(self):
            pass
        def get_chunks(self) -> list[bytes]:
            c = self.chunks
            self.chunks = []
            return c

    folder_res = resolve_photo_folder_path(date_folder, settings)
    if folder_res[0] == "err":
        return
    folder_path = folder_res[1]

    buffer = FileLikeGenerator()
    missing_files = []
    
    target_entries = index.entries if not selection else [index.by_name[s] for s in selection if s in index.by_name]
    
    try:
        with zipfile.ZipFile(buffer, mode="w", compression=zipfile.ZIP_STORED, allowZip64=True) as zf:
            for entry in target_entries:
                filepath = folder_path / entry.name
                try:
                    with open(filepath, "rb") as f:
                        zinfo = zipfile.ZipInfo(filename=entry.name)
                        zinfo.file_size = entry.size_bytes   # drives the ZIP64 local-header decision
                        _mt = time.localtime(entry.mtime_ns / 1e9)[:6]
                        zinfo.date_time = _mt if _mt[0] >= 1980 else (1980, 1, 1, 0, 0, 0)
                        with zf.open(zinfo, mode="w") as z_out:
                            while True:
                                chunk = f.read(65536)
                                if not chunk:
                                    break
                                z_out.write(chunk)
                                yield from buffer.get_chunks()
                except OSError as e:
                    logger.warning("Archive streaming skipped %s: %s", entry.name, e)
                    missing_files.append(entry.name)

            if missing_files:
                zinfo = zipfile.ZipInfo(filename="_MISSING.txt")
                missing_name = "_MISSING.txt"
                if missing_name in [e.name for e in target_entries]:
                    missing_name = f"_MISSING_{time.time_ns()}.txt"
                    zinfo.filename = missing_name
                
                content = "The following files could not be read and are missing from this archive:\n" + "\n".join(missing_files)
                zf.writestr(zinfo, content)
                yield from buffer.get_chunks()
                
            if not selection and index.truncated:
                zinfo = zipfile.ZipInfo(filename="_TRUNCATED.txt")
                trunc_name = "_TRUNCATED.txt"
                if trunc_name in [e.name for e in target_entries]:
                    trunc_name = f"_TRUNCATED_{time.time_ns()}.txt"
                    zinfo.filename = trunc_name
                
                content = f"Listing was truncated to {settings.shipping_photos_max_files_per_folder} files."
                zf.writestr(zinfo, content)
                yield from buffer.get_chunks()
                
        yield from buffer.get_chunks()
    finally:
        pass
