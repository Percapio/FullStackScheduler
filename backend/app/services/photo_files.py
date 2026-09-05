import os
import re
import stat
import time
import zipfile
from collections import OrderedDict
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from threading import RLock
from typing import Callable, Iterator, List, Dict, Literal, Union, Optional, Tuple, Set

from ..config import Settings
from .shipping_photos import resolve_photo_folder_path, is_photo_folder_name

FolderName = str
SubFolder = FolderName
FolderKey = Tuple[str, SubFolder]

ROOT: SubFolder = ""

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
    key: FolderKey
    status: PhotoFileListStatus
    entries: List[PhotoFileEntry]
    by_name: Dict[FileName, PhotoFileEntry]
    folders: List[FolderName]
    folder_set: Set[FolderName]
    total_bytes: int
    scanned_at: float
    truncated: bool
    folders_truncated: bool

PhotoFileFailure = Literal[
    "unconfigured",
    "unavailable",
    "folder_not_found",
    "sub_folder_not_found",
    "index_mismatch",
    "file_not_found",
    "not_a_file"
]

FILE_NAME_PREFILTER = re.compile("^[^\\x00-\\x1f<>:\"/\\\\|?*]{1,255}$")

def is_plausible_file_name(candidate: str) -> bool:
    if not candidate:
        return False
    return bool(FILE_NAME_PREFILTER.match(candidate))

def is_plausible_folder_name(candidate: FolderName) -> bool:
    if not candidate:
        return False
    if candidate in (".", ".."):
        return False
    if candidate.endswith(".") or candidate.endswith(" "):
        return False
    if not FILE_NAME_PREFILTER.match(candidate):
        return False
    stem = candidate.split(".")[0].upper()
    reserved = {"CON", "PRN", "AUX", "NUL"}
    for i in range(1, 10):
        reserved.add(f"COM{i}")
        reserved.add(f"LPT{i}")
    if stem in reserved:
        return False
    return True

def resolve_photo_file_path(
    date_folder: str,
    sub_folder: SubFolder,
    file_name: FileName,
    index: PhotoFileIndex,
    settings: Settings,
    resolve: Callable[[Path], Path] = lambda p: p.resolve(),
    stat_fn: Callable[[Path], FileStatus] = lambda p: _default_stat(p)
) -> Union[Tuple[Literal["ok"], Path], Tuple[Literal["err"], PhotoFileFailure]]:
    
    # 0. Key agreement
    if index.key != (date_folder, sub_folder):
        import logging
        logging.getLogger(__name__).error("index.key %r does not match (%r, %r)", index.key, date_folder, sub_folder)
        return "err", "index_mismatch"

    # 1. Resolve date folder
    folder_res = resolve_photo_folder_path(date_folder, settings)
    if folder_res[0] == "err":
        if folder_res[1] == "unconfigured":
            return "err", "unconfigured"
        elif folder_res[1] == "unavailable":
            return "err", "unavailable"
        else: # invalid_name or not_found
            return "err", "folder_not_found"
            
    resolved_date_root = folder_res[1]
    
    # 1b. Resolve sub_folder
    if sub_folder == ROOT:
        resolved_folder = resolved_date_root
    else:
        if not is_plausible_folder_name(sub_folder):
            return "err", "sub_folder_not_found"
        
        root_index = resolve_file_index(date_folder, ROOT, settings, time.monotonic)
        if sub_folder not in root_index.folder_set:
            return "err", "sub_folder_not_found"
            
        resolved_folder = resolved_date_root / sub_folder
        try:
            resolved_folder = resolve(resolved_folder)
            resolved_date_root_abs = resolve(resolved_date_root)
            resolved_folder.relative_to(resolved_date_root_abs)
        except (OSError, ValueError):
            return "err", "sub_folder_not_found"

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

_file_index_lock = RLock()
_file_indexes: OrderedDict[FolderKey, PhotoFileIndex] = OrderedDict()

PREVIEWABLE_EXTENSIONS: Dict[Extension, MediaType] = {
    ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
    ".png": "image/png",  ".webp": "image/webp",
    ".gif": "image/gif"
}

def resolve_file_index(
    date_folder: str,
    sub_folder: SubFolder,
    settings: Settings,
    clock: Callable[[], float]
) -> PhotoFileIndex:
    now = clock()
    key = (date_folder, sub_folder)
    
    with _file_index_lock:
        if key in _file_indexes:
            cached = _file_indexes[key]
            age = now - cached.scanned_at
            
            if cached.status == PhotoFileListStatus.OK:
                if age < settings.shipping_photos_file_index_ttl_seconds:
                    _file_indexes.move_to_end(key)
                    return cached
            else:
                if age < settings.shipping_photos_file_unavailable_ttl_seconds:
                    _file_indexes.move_to_end(key)
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
                key=key,
                status=status,
                entries=[],
                by_name={},
                folders=[],
                folder_set=set(),
                total_bytes=0,
                scanned_at=now,
                truncated=False,
                folders_truncated=False
            )
        else:
            resolved_date_root = folder_res[1]
            
            if sub_folder == ROOT:
                folder_path = resolved_date_root
            else:
                if not is_plausible_folder_name(sub_folder):
                    idx = PhotoFileIndex(
                        key=key, status=PhotoFileListStatus.NOT_FOUND,
                        entries=[], by_name={}, folders=[], folder_set=set(),
                        total_bytes=0, scanned_at=now, truncated=False, folders_truncated=False
                    )
                    _file_indexes[key] = idx
                    _file_indexes.move_to_end(key)
                    return idx
                
                root_idx = resolve_file_index(date_folder, ROOT, settings, clock)
                if sub_folder not in root_idx.folder_set:
                    idx = PhotoFileIndex(
                        key=key, status=PhotoFileListStatus.NOT_FOUND,
                        entries=[], by_name={}, folders=[], folder_set=set(),
                        total_bytes=0, scanned_at=now, truncated=False, folders_truncated=False
                    )
                    _file_indexes[key] = idx
                    _file_indexes.move_to_end(key)
                    return idx
                    
                folder_path = resolved_date_root / sub_folder
                try:
                    folder_path = folder_path.resolve()
                    resolved_date_root_abs = resolved_date_root.resolve()
                    folder_path.relative_to(resolved_date_root_abs)
                except (OSError, ValueError):
                    idx = PhotoFileIndex(
                        key=key, status=PhotoFileListStatus.NOT_FOUND,
                        entries=[], by_name={}, folders=[], folder_set=set(),
                        total_bytes=0, scanned_at=now, truncated=False, folders_truncated=False
                    )
                    _file_indexes[key] = idx
                    _file_indexes.move_to_end(key)
                    return idx

            try:
                iterator = os.scandir(folder_path)
            except OSError:
                idx = PhotoFileIndex(
                    key=key,
                    status=PhotoFileListStatus.NOT_FOUND,
                    entries=[],
                    by_name={},
                    folders=[],
                    folder_set=set(),
                    total_bytes=0,
                    scanned_at=now,
                    truncated=False,
                    folders_truncated=False
                )
            else:
                entries = []
                folders = []
                total_bytes = 0
                truncated = False
                folders_truncated = False
                
                with iterator:
                    for entry in iterator:
                        try:
                            if entry.is_dir(follow_symlinks=False) and sub_folder == ROOT:
                                if not (entry.stat(follow_symlinks=False).st_file_attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT):
                                    folders.append(entry.name)
                            elif entry.is_file(): # which checks is_regular_file
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
                        except AttributeError:
                            # st_file_attributes might not exist on non-windows
                            # Fallback if needed
                            if entry.is_dir(follow_symlinks=False) and sub_folder == ROOT:
                                folders.append(entry.name)
                
                # ASCII-friendly lexicographical sort
                entries.sort(key=lambda e: e.name)
                folders.sort()
                
                if len(entries) > settings.shipping_photos_max_files_per_folder:
                    entries = entries[:settings.shipping_photos_max_files_per_folder]
                    truncated = True
                    
                if len(folders) > settings.shipping_photos_max_subfolders_per_date:
                    folders = folders[:settings.shipping_photos_max_subfolders_per_date]
                    folders_truncated = True
                
                for e in entries:
                    total_bytes += e.size_bytes
                    
                by_name = {e.name: e for e in entries}
                folder_set = set(folders)
                
                idx = PhotoFileIndex(
                    key=key,
                    status=PhotoFileListStatus.OK,
                    entries=entries,
                    by_name=by_name,
                    folders=folders,
                    folder_set=folder_set,
                    total_bytes=total_bytes,
                    scanned_at=now,
                    truncated=truncated,
                    folders_truncated=folders_truncated
                )
        
        _file_indexes[key] = idx
        _file_indexes.move_to_end(key)
        
        if len(_file_indexes) > settings.shipping_photos_file_index_max_keys:
            _file_indexes.popitem(last=False)
            
        return idx

class ALL_FOLDERS:
    pass

def invalidate_file_index(target: Union[str, FolderKey, ALL_FOLDERS]) -> None:
    if not isinstance(target, (str, tuple, ALL_FOLDERS)):
        raise TypeError(f"Invalidate target unrecognised type {type(target)}")
    with _file_index_lock:
        if isinstance(target, ALL_FOLDERS):
            _file_indexes.clear()
        elif isinstance(target, str): # DateFolder
            keys_to_remove = [k for k in _file_indexes if k[0] == target]
            for k in keys_to_remove:
                _file_indexes.pop(k, None)
        else: # FolderKey
            _file_indexes.pop(target, None)

import logging
logger = logging.getLogger(__name__)

def stream_photo_archive(
    date_folder: str,
    sub_folder: SubFolder,
    selection: List[FileName],
    index: PhotoFileIndex,
    settings: Settings
) -> Iterator[bytes]:
    
    if index.key != (date_folder, sub_folder):
        logger.error("index.key %r does not match (%r, %r)", index.key, date_folder, sub_folder)
        return
        
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
    
    if sub_folder != ROOT:
        folder_path = folder_path / sub_folder

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
