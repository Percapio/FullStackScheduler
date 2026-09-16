import threading
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


import asyncio
import queue
import time
from typing import AsyncIterator
import threading
from dataclasses import dataclass
from typing import List, Union, Literal, Optional
import zipfile

ReadFailure = Literal["Unreadable", "Vanished"]
AbandonCause = Literal["ConsumerStalled", "SessionReleased"]

class ArchiveReadItem:
    pass

@dataclass
class FileOpened(ArchiveReadItem):
    entry: PhotoFileEntry

@dataclass
class FileChunk(ArchiveReadItem):
    payload: bytes

@dataclass
class FileFinished(ArchiveReadItem):
    entry: PhotoFileEntry

@dataclass
class FileUnreadable(ArchiveReadItem):
    name: FileName
    cause: ReadFailure
    entry_was_open: bool

class StreamFinished(ArchiveReadItem):
    pass

@dataclass
class StreamAbandoned(ArchiveReadItem):
    cause: AbandonCause

@dataclass
class ArchiveSnapshot:
    entries: List[PhotoFileEntry]
    unresolved: List[FileName]
    covers_full_listing: bool
    index_truncated: bool
    scanned_at: float

class ArchiveTransport:
    def __init__(self, data_credits: int, loop: asyncio.AbstractEventLoop):
        self.items = asyncio.Queue()
        self.data_credits = threading.Semaphore(data_credits)
        self.terminal_credit = threading.Semaphore(1)
        self.cancel_mirror = asyncio.Event()
        self.loop = loop

def publish(transport: ArchiveTransport, item: ArchiveReadItem, cancel: threading.Event, poll_seconds: float) -> bool:
    while True:
        if cancel.is_set():
            return False
        if transport.data_credits.acquire(timeout=poll_seconds):
            try:
                transport.loop.call_soon_threadsafe(transport.items.put_nowait, item)
            except RuntimeError:
                pass
            return True

def publish_terminal(transport: ArchiveTransport, item: Union[StreamFinished, StreamAbandoned]) -> None:
    if transport.terminal_credit.acquire(blocking=False):
        try:
            transport.loop.call_soon_threadsafe(transport.items.put_nowait, item)
        except RuntimeError:
            pass

async def take(transport: ArchiveTransport) -> Union[ArchiveReadItem, None]:
    take_task = asyncio.create_task(transport.items.get())
    cancel_task = asyncio.create_task(transport.cancel_mirror.wait())
    done, pending = await asyncio.wait([take_task, cancel_task], return_when=asyncio.FIRST_COMPLETED)
    
    if cancel_task in done:
        take_task.cancel()
        while not transport.items.empty():
            transport.items.get_nowait()
        return None
        
    item = take_task.result()
    if not isinstance(item, (StreamFinished, StreamAbandoned)):
        transport.data_credits.release()
    return item

def signal_cancel(transport: ArchiveTransport) -> None:
    try:
        transport.loop.call_soon_threadsafe(transport.cancel_mirror.set)
    except RuntimeError:
        pass

class ArchivePermits:
    def __init__(self, capacity: int, reader_ceiling: int):
        self.capacity = capacity
        self.reader_ceiling = reader_ceiling
        self.in_use = 0
        self.live_readers = 0
        self.guard = threading.Lock()

class SessionLease:
    def __init__(self, permits: ArchivePermits):
        self.permits = permits
        self.permit_returned = False
        self.reader_returned = False

class PermitsExhausted(Exception): pass
class ReaderBacklog(Exception): pass

def try_admit(permits: ArchivePermits) -> SessionLease:
    with permits.guard:
        if permits.in_use >= permits.capacity:
            raise PermitsExhausted()
        if permits.live_readers >= permits.reader_ceiling:
            raise ReaderBacklog()
        permits.in_use += 1
        permits.live_readers += 1
        return SessionLease(permits)

def return_permit(lease: SessionLease) -> None:
    if not lease.permit_returned:
        with lease.permits.guard:
            lease.permits.in_use -= 1
        lease.permit_returned = True

def return_reader_slot(lease: SessionLease) -> None:
    if not lease.reader_returned:
        with lease.permits.guard:
            lease.permits.live_readers -= 1
        lease.reader_returned = True

SessionOutcome = Literal[
    "Completed", "FailedFraming", "AbandonedDisconnect", 
    "AbandonedBudget", "AbandonedStall", "FailedStart", "TicketRefused"
]

class ArchiveStreamSession:
    def __init__(self, session_id: str, transport: ArchiveTransport, lease: SessionLease, snapshot: ArchiveSnapshot, token: str, date_folder: str, sub_folder: str):
        self.session_id = session_id
        self.transport = transport
        self.cancel = threading.Event()
        self.lease = lease
        self.snapshot = snapshot
        self.token = token
        self.date_folder = date_folder
        self.sub_folder = sub_folder
        self.reader = None
        self.bytes_sent = 0
        self.pending_outcome = None
        self.budget_handle = None
        self.released = False
        self.guard = threading.Lock()
        self.start_time = time.monotonic()

def release(session: ArchiveStreamSession, outcome: SessionOutcome) -> None:
    with session.guard:
        if session.released:
            return
        session.released = True
        
    session.cancel.set()
    signal_cancel(session.transport)
    
    if session.budget_handle:
        session.budget_handle.cancel()
        
    return_permit(session.lease)
    
    from .archive_tokens import settle_ticket
    settle_ticket(session.token, session.session_id, session.bytes_sent)
    
    duration_ms = (time.monotonic() - session.start_time) * 1000.0
    import logging
    logger = logging.getLogger("scheduler")
    level = logging.INFO if outcome in ("Completed", "AbandonedDisconnect") else logging.WARNING
    
    snapshot_bytes = sum(e.size_bytes for e in session.snapshot.entries)
    
    logger.log(level, "ArchiveSessionRecord: session_id=%s date_folder=%s sub_folder=%s entry_count=%d unresolved_count=%d snapshot_bytes=%d bytes_sent=%d permits_in_use=%d live_readers=%d duration_ms=%.1f outcome=%s",
        session.session_id, session.date_folder, session.sub_folder, len(session.snapshot.entries), len(session.snapshot.unresolved), snapshot_bytes, session.bytes_sent, session.lease.permits.in_use, session.lease.permits.live_readers, duration_ms, outcome
    )

    from ..config import get_settings
    from .archive_status import record_status
    
    record_status(
        token=session.token,
        state="Terminal",
        outcome=outcome,
        bytes_sent=session.bytes_sent,
        entry_count=len(session.snapshot.entries),
        unresolved_count=len(session.snapshot.unresolved),
        minted_loopback=False, # We don't have minted_loopback here easily, but the status store already has it from 'Streaming' write! Wait.
        settings=get_settings(),
        clock=time.monotonic()
    )

def classify_read_failure(cause: OSError) -> ReadFailure:
    if isinstance(cause, FileNotFoundError):
        return "Vanished"
    return "Unreadable"

def archive_reader_loop(
    session: ArchiveStreamSession,
    folder_path: Path,
    snapshot: ArchiveSnapshot,
    settings: Settings
) -> None:
    try:
        chunk_size = settings.shipping_photos_archive_read_chunk_bytes
        poll_seconds = settings.shipping_photos_archive_credit_poll_seconds
        
        abandoned = False
        
        for name in snapshot.unresolved:
            if not publish(session.transport, FileUnreadable(name, "Vanished", False), session.cancel, poll_seconds):
                abandoned = True
                break
        
        if not abandoned:
            for entry in snapshot.entries:
                if session.cancel.is_set():
                    abandoned = True
                    break
                    
                filepath = folder_path / entry.name
                entry_was_open = False
                try:
                    with open(filepath, "rb") as f:
                        entry_was_open = True
                        if not publish(session.transport, FileOpened(entry), session.cancel, poll_seconds):
                            abandoned = True
                            break
                            
                        while True:
                            if session.cancel.is_set():
                                abandoned = True
                                break
                            chunk = f.read(chunk_size)
                            if not chunk:
                                break
                            if not publish(session.transport, FileChunk(chunk), session.cancel, poll_seconds):
                                abandoned = True
                                break
                                
                        if abandoned:
                            break
                        if not publish(session.transport, FileFinished(entry), session.cancel, poll_seconds):
                            abandoned = True
                            break
                except OSError as e:
                    cause = classify_read_failure(e)
                    if not publish(session.transport, FileUnreadable(entry.name, cause, entry_was_open), session.cancel, poll_seconds):
                        abandoned = True
                        break
                        
        if abandoned:
            cause = "SessionReleased" if session.cancel.is_set() else "ConsumerStalled"
            publish_terminal(session.transport, StreamAbandoned(cause))
            if cause == "ConsumerStalled":
                release(session, "AbandonedStall")
            else:
                release(session, "AbandonedDisconnect")
        else:
            publish_terminal(session.transport, StreamFinished())
    finally:
        return_reader_slot(session.lease)

async def stream_photo_archive(
    date_folder: str,
    sub_folder: SubFolder,
    snapshot: ArchiveSnapshot,
    settings: Settings,
    session: ArchiveStreamSession
) -> AsyncIterator[bytes]:
    
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

    buffer = FileLikeGenerator()
    missing_files_vanished = []
    missing_files_unreadable = []
    
    try:
        with zipfile.ZipFile(buffer, mode="w", compression=zipfile.ZIP_STORED, allowZip64=True) as zf:
            z_out = None
            while True:
                item = await take(session.transport)
                if item is None:
                    if z_out is not None:
                        z_out.close()
                        z_out = None
                    break
                    
                if isinstance(item, FileOpened):
                    if z_out is not None:
                        z_out.close()
                    entry = item.entry
                    zinfo = zipfile.ZipInfo(filename=entry.name)
                    zinfo.file_size = entry.size_bytes
                    _mt = time.localtime(entry.mtime_ns / 1e9)[:6]
                    zinfo.date_time = _mt if _mt[0] >= 1980 else (1980, 1, 1, 0, 0, 0)
                    z_out = zf.open(zinfo, mode="w")
                elif isinstance(item, FileChunk):
                    z_out.write(item.payload)
                    chunks = buffer.get_chunks()
                    if chunks:
                        for chunk in chunks:
                            yield chunk
                            session.bytes_sent += len(chunk)
                elif isinstance(item, FileFinished):
                    z_out.close()
                    z_out = None
                    chunks = buffer.get_chunks()
                    if chunks:
                        for chunk in chunks:
                            yield chunk
                            session.bytes_sent += len(chunk)
                elif isinstance(item, FileUnreadable):
                    if item.entry_was_open and z_out is not None:
                        z_out.close()
                        z_out = None
                    if item.cause == "Vanished":
                        missing_files_vanished.append(item.name)
                    else:
                        missing_files_unreadable.append(item.name)
                elif isinstance(item, StreamFinished):
                    session.pending_outcome = "Completed"
                    break
                elif isinstance(item, StreamAbandoned):
                    if z_out is not None:
                        z_out.close()
                        z_out = None
                    session.pending_outcome = item.cause
                    break
            
            target_names = [e.name for e in snapshot.entries] + snapshot.unresolved
            if missing_files_vanished or missing_files_unreadable:
                zinfo = zipfile.ZipInfo(filename="_MISSING.txt")
                missing_name = "_MISSING.txt"
                if missing_name in target_names:
                    missing_name = f"_MISSING_{time.time_ns()}.txt"
                    zinfo.filename = missing_name
                
                lines = ["The following files are missing or incomplete in this archive:"]
                for n in missing_files_vanished:
                    lines.append(f"  {n} — moved or deleted before the archive was built")
                for n in missing_files_unreadable:
                    lines.append(f"  {n} — could not be read from the photos folder")
                    
                zf.writestr(zinfo, "\\n".join(lines))
                chunks = buffer.get_chunks()
                if chunks:
                    for chunk in chunks:
                        yield chunk
                        session.bytes_sent += len(chunk)
                        
            if snapshot.covers_full_listing and snapshot.index_truncated:
                zinfo = zipfile.ZipInfo(filename="_TRUNCATED.txt")
                trunc_name = "_TRUNCATED.txt"
                if trunc_name in target_names:
                    trunc_name = f"_TRUNCATED_{time.time_ns()}.txt"
                    zinfo.filename = trunc_name
                
                content = f"Listing was truncated to {settings.shipping_photos_max_files_per_folder} files."
                zf.writestr(zinfo, content)
                chunks = buffer.get_chunks()
                if chunks:
                    for chunk in chunks:
                        yield chunk
                        session.bytes_sent += len(chunk)
                        
        chunks = buffer.get_chunks()
        if chunks:
            if session.pending_outcome == "Completed":
                for chunk in chunks:
                    yield chunk
                    session.bytes_sent += len(chunk)
            
    except Exception:
        session.pending_outcome = "FailedFraming"
    finally:
        outcome = session.pending_outcome or "AbandonedDisconnect"
        if outcome == "ConsumerStalled": outcome = "AbandonedStall"
        elif outcome == "SessionReleased": outcome = "AbandonedDisconnect"
            
        release(session, outcome)
