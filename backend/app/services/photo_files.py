import threading
import os
import re
import stat
import time
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


# =============================================================================
# Archive redemption (Phase 30, 31, 32)
# =============================================================================

import asyncio
import secrets
import zlib
from typing import Any, AsyncIterator

import anyio

from .archive_zip import (
    ArchivePlan, CentralHeader, DataDescriptor, ExcludedEntry, InlineContent,
    LocalHeader, MemberSpec, dos_timestamp, encode_record, end_records,
    first_free_name, inline_member, lay_out, missing_manifest, truncated_notice,
)

ExclusionCause = Literal["Vanished", "Unreadable", "OutsideFolder", "NotRegularFile"]
DivergenceCause = Literal["Vanished", "Unreadable", "Replaced", "Truncated", "Extended"]
AbandonCause = Literal["ConsumerStalled", "SessionReleased"]

DIRECTORY_FRAME_BYTES = 1 << 20


# ---- Snapshot (§6.3) --------------------------------------------------------

@dataclass
class ArchiveSnapshot:
    entries: List[PhotoFileEntry]
    unresolved: List[FileName]
    covers_full_listing: bool
    index_truncated: bool
    scanned_at: float


def build_snapshot(selection: List[FileName], index: PhotoFileIndex) -> ArchiveSnapshot:
    """Everything one redemption considers, from exactly one index resolution.

    covers_full_listing is membership, not a count: every listing entry must be
    selected, and unresolved names do not count toward it (N4).
    """
    if not selection:
        entries = list(index.entries)
        unresolved: List[FileName] = []
    else:
        entries = []
        unresolved = []
        seen: Set[FileName] = set()
        for name in selection:
            if name in seen:
                continue
            seen.add(name)
            if name in index.by_name:
                entries.append(index.by_name[name])
            else:
                unresolved.append(name)
    selected = {e.name for e in entries}
    covers_full_listing = all(e.name in selected for e in index.entries)
    return ArchiveSnapshot(
        entries=entries,
        unresolved=unresolved,
        covers_full_listing=covers_full_listing,
        index_truncated=index.truncated,
        scanned_at=index.scanned_at,
    )


# ---- Identity and probing (§7.2) --------------------------------------------

@dataclass(frozen=True)
class FileIdentity:
    size: int
    mtime_ns: int
    file_id: Optional[Tuple[int, int]]


def identity_from_stat(st: os.stat_result) -> FileIdentity:
    file_id = (st.st_dev, st.st_ino) if st.st_ino else None
    return FileIdentity(size=st.st_size, mtime_ns=st.st_mtime_ns, file_id=file_id)


def same_identity(expected: FileIdentity, observed: FileIdentity) -> bool:
    if expected.size != observed.size or expected.mtime_ns != observed.mtime_ns:
        return False
    if expected.file_id is not None and observed.file_id is not None:
        return expected.file_id == observed.file_id
    return True


def divergence_between(expected: FileIdentity, observed: FileIdentity) -> DivergenceCause:
    if expected.file_id is not None and observed.file_id is not None and expected.file_id != observed.file_id:
        return "Replaced"
    if observed.size > expected.size:
        return "Extended"
    if observed.size < expected.size:
        return "Truncated"
    return "Replaced"


@dataclass(frozen=True)
class ProbedEntry:
    entry: PhotoFileEntry
    resolved: Path
    identity: FileIdentity


@dataclass(frozen=True)
class Admitted:
    probed: ProbedEntry


@dataclass(frozen=True)
class Excluded:
    cause: ExclusionCause


ProbeResult = Union[Admitted, Excluded]


def _default_open(path: Path):
    return open(path, "rb")


def probe_entry(
    folder: Path,
    entry: PhotoFileEntry,
    resolve: Callable[[Path], Path] = lambda p: p.resolve(),
    stat_fn: Callable[[Path], os.stat_result] = os.stat,
    open_fn: Callable[[Path], Any] = _default_open,
    attempts: int = 2
) -> ProbeResult:
    """Establishes whether one entry can be archived and exactly what it contributes.

    folder must already be fully resolved. Containment, regular-file, open, and
    identity checks run in that order; an identity mismatch re-probes once.
    """
    try:
        resolved = resolve(folder / entry.name)
    except FileNotFoundError:
        return Excluded("Vanished")
    except OSError:
        return Excluded("Unreadable")
    try:
        resolved.relative_to(folder)
    except ValueError:
        return Excluded("OutsideFolder")

    try:
        path_stat = stat_fn(resolved)
    except FileNotFoundError:
        return Excluded("Vanished")
    except OSError:
        return Excluded("Unreadable")
    if not stat.S_ISREG(path_stat.st_mode):
        return Excluded("NotRegularFile")

    try:
        handle = open_fn(resolved)
    except FileNotFoundError:
        return Excluded("Vanished")
    except OSError:
        return Excluded("Unreadable")
    try:
        handle_identity = identity_from_stat(os.fstat(handle.fileno()))
    except OSError:
        return Excluded("Unreadable")
    finally:
        handle.close()

    if not same_identity(identity_from_stat(path_stat), handle_identity):
        if attempts > 1:
            return probe_entry(folder, entry, resolve, stat_fn, open_fn, attempts - 1)
        return Excluded("Unreadable")
    return Admitted(ProbedEntry(entry=entry, resolved=resolved, identity=handle_identity))


# ---- The plan (§9) ----------------------------------------------------------

@dataclass(frozen=True)
class AdmittedFile:
    probed: ProbedEntry


def build_archive_plan(
    snapshot: ArchiveSnapshot,
    probes: List[ProbeResult],
    max_files_per_folder: int
) -> ArchivePlan:
    """Builds the plan. No I/O, no clock, no randomness."""
    if len(probes) != len(snapshot.entries):
        raise ValueError("one probe result per snapshot entry is required")

    excluded: List[ExcludedEntry] = [ExcludedEntry(name, "Vanished") for name in snapshot.unresolved]
    specs: List[MemberSpec] = []
    for entry, probe in zip(snapshot.entries, probes):
        if isinstance(probe, Admitted):
            identity = probe.probed.identity
            specs.append(MemberSpec(entry.name, identity.size, dos_timestamp(identity.mtime_ns), AdmittedFile(probe.probed)))
        else:
            excluded.append(ExcludedEntry(entry.name, probe.cause))

    taken = [spec.name for spec in specs]
    if excluded:
        manifest_name = first_free_name("_MISSING", taken)
        specs.append(inline_member(manifest_name, missing_manifest(excluded)))
        taken.append(manifest_name)
    if snapshot.covers_full_listing and snapshot.index_truncated:
        specs.append(inline_member(first_free_name("_TRUNCATED", taken), truncated_notice(max_files_per_folder)))

    return lay_out(specs, excluded)


# ---- Transport (Phase 30 §4.2) ----------------------------------------------

class ArchiveReadItem:
    pass


@dataclass
class MemberBegin(ArchiveReadItem):
    index: int


@dataclass
class MemberChunk(ArchiveReadItem):
    payload: bytes


@dataclass
class MemberEnd(ArchiveReadItem):
    index: int


class StreamFinished(ArchiveReadItem):
    pass


@dataclass
class StreamAbandoned(ArchiveReadItem):
    cause: AbandonCause


@dataclass
class SourceDiverged(ArchiveReadItem):
    name: FileName
    cause: DivergenceCause


TERMINAL_ITEMS = (StreamFinished, StreamAbandoned, SourceDiverged)


class ArchiveTransport:
    def __init__(self, data_credits: int, loop: asyncio.AbstractEventLoop):
        self.items: asyncio.Queue = asyncio.Queue()
        self.data_credits = threading.Semaphore(data_credits)
        self.terminal_credit = threading.Semaphore(1)
        self.cancel_mirror = asyncio.Event()
        self.loop = loop


PublishResult = Literal["Published", "Cancelled", "Stalled"]


def publish(
    transport: ArchiveTransport,
    item: ArchiveReadItem,
    cancel: threading.Event,
    poll_seconds: float,
    stall_seconds: float = float("inf")
) -> PublishResult:
    """Blocks the reader until a credit is free, then publishes without blocking.

    Stalled when no credit came back within stall_seconds of this call: the
    consumer has stopped taking (reader_stall_seconds, Phase 27 §3.3).
    """
    deadline = time.monotonic() + stall_seconds
    while True:
        if cancel.is_set():
            return "Cancelled"
        if transport.data_credits.acquire(timeout=poll_seconds):
            try:
                transport.loop.call_soon_threadsafe(transport.items.put_nowait, item)
            except RuntimeError:
                return "Cancelled"
            return "Published"
        if time.monotonic() >= deadline:
            return "Stalled"


def publish_terminal(transport: ArchiveTransport, item: ArchiveReadItem) -> None:
    if transport.terminal_credit.acquire(blocking=False):
        try:
            transport.loop.call_soon_threadsafe(transport.items.put_nowait, item)
        except RuntimeError:
            pass


async def take(transport: ArchiveTransport) -> Optional[ArchiveReadItem]:
    """Next item, or None once teardown began. Selects on the cancel mirror, so
    a reader that publishes nothing never blocks the emitter past release."""
    if transport.cancel_mirror.is_set():
        return None
    take_task = asyncio.ensure_future(transport.items.get())
    cancel_task = asyncio.ensure_future(transport.cancel_mirror.wait())
    try:
        await asyncio.wait([take_task, cancel_task], return_when=asyncio.FIRST_COMPLETED)
    finally:
        cancel_task.cancel()
        if not take_task.done():
            take_task.cancel()
    if not take_task.done() or take_task.cancelled():
        return None
    item = take_task.result()
    if transport.cancel_mirror.is_set():
        return None
    if not isinstance(item, TERMINAL_ITEMS):
        transport.data_credits.release()
    return item


def signal_cancel(transport: ArchiveTransport) -> None:
    try:
        transport.loop.call_soon_threadsafe(transport.cancel_mirror.set)
    except RuntimeError:
        pass


# ---- Permits ----------------------------------------------------------------

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


class PermitsExhausted(Exception):
    pass


class ReaderBacklog(Exception):
    pass


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
    with lease.permits.guard:
        if not lease.permit_returned:
            lease.permits.in_use -= 1
            lease.permit_returned = True


def return_reader_slot(lease: SessionLease) -> None:
    with lease.permits.guard:
        if not lease.reader_returned:
            lease.permits.live_readers -= 1
            lease.reader_returned = True


# ---- Session ----------------------------------------------------------------

SessionOutcome = Literal[
    "Completed", "AbandonedDisconnect", "AbandonedBudget", "AbandonedStall",
    "FailedFraming", "TicketRefused", "FailedStart", "FolderNotFound",
    "ListingUnavailable", "SourceChanged", "PreflightStalled"
]


class ArchiveStreamSession:
    def __init__(
        self,
        session_id: str,
        transport: ArchiveTransport,
        lease: SessionLease,
        token: str,
        date_folder: str,
        sub_folder: SubFolder,
        selection: List[FileName],
        minted_loopback: bool,
        settings: Settings
    ):
        self.session_id = session_id
        self.transport = transport
        self.cancel = threading.Event()
        self.lease = lease
        self.token = token
        self.date_folder = date_folder
        self.sub_folder = sub_folder
        self.selection = selection
        self.minted_loopback = minted_loopback
        self.settings = settings
        self.reader: Optional[threading.Thread] = None
        self.snapshot: Optional[ArchiveSnapshot] = None
        self.plan: Optional[ArchivePlan] = None
        self.plan_outcome: Optional["PreflightResult"] = None
        self.preflight_signal = asyncio.Event()
        self.preflight_units = 0
        self.preflight_ms: Optional[float] = None
        self.divergence_cause: Optional[DivergenceCause] = None
        self.bytes_sent = 0
        self.outcome: Optional[SessionOutcome] = None
        self.budget_handle: Optional[asyncio.TimerHandle] = None
        self.released = False
        self.guard = threading.Lock()
        self.start_time = time.monotonic()


def record_outcome(session: ArchiveStreamSession, outcome: SessionOutcome) -> Literal["Recorded", "AlreadyRecorded"]:
    """Records why the session ended. First writer wins (§4)."""
    with session.guard:
        if session.outcome is not None:
            return "AlreadyRecorded"
        session.outcome = outcome
        return "Recorded"


def _cancel_budget(session: ArchiveStreamSession) -> None:
    handle = session.budget_handle
    if handle is None:
        return
    try:
        session.transport.loop.call_soon_threadsafe(handle.cancel)
    except RuntimeError:
        handle.cancel()


def release(session: ArchiveStreamSession, outcome: SessionOutcome) -> None:
    """Surrenders the permit, settles the ticket, disarms the budget, and records
    the outcome. Idempotent, synchronous, and never awaits (Phase 30 §4.3)."""
    record_outcome(session, outcome)
    with session.guard:
        if session.released:
            return
        session.released = True
    final_outcome = session.outcome

    session.cancel.set()
    signal_cancel(session.transport)
    _cancel_budget(session)
    return_permit(session.lease)

    from .archive_tokens import settle_ticket
    settle_ticket(session.token, session.session_id, session.bytes_sent)

    plan = session.plan
    snapshot = session.snapshot
    if plan is not None:
        entry_count = plan.file_member_count
        missing_count = len(plan.excluded)
    elif snapshot is not None:
        entry_count = len(snapshot.entries)
        missing_count = len(snapshot.unresolved)
    else:
        entry_count = 0
        missing_count = 0

    duration_ms = (time.monotonic() - session.start_time) * 1000.0
    level = logging.INFO if final_outcome in ("Completed", "AbandonedDisconnect") else logging.WARNING
    logging.getLogger("scheduler").log(
        level,
        "ArchiveSessionRecord: session_id=%s date_folder=%s sub_folder=%s entry_count=%d unresolved_count=%d "
        "declared_bytes=%s bytes_sent=%d preflight_ms=%s excluded_count=%d divergence_cause=%s "
        "permits_in_use=%d live_readers=%d duration_ms=%.1f outcome=%s",
        session.session_id, session.date_folder, session.sub_folder, entry_count, missing_count,
        plan.declared_bytes if plan is not None else None, session.bytes_sent,
        f"{session.preflight_ms:.1f}" if session.preflight_ms is not None else None,
        len(plan.excluded) if plan is not None else 0, session.divergence_cause,
        session.lease.permits.in_use, session.lease.permits.live_readers, duration_ms, final_outcome
    )

    from .archive_status import record_status
    record_status(
        token=session.token,
        state="Terminal",
        outcome=final_outcome,
        bytes_sent=session.bytes_sent,
        entry_count=entry_count,
        unresolved_count=missing_count,
        minted_loopback=session.minted_loopback,
        settings=session.settings,
        clock=time.monotonic
    )


# ---- Preflight on the reader (§6, §7) ---------------------------------------

@dataclass(frozen=True)
class PlanReady:
    plan: ArchivePlan


@dataclass(frozen=True)
class PlanRefused:
    reason: Literal["FolderNotFound", "ListingUnavailable"]
    kind: str


@dataclass(frozen=True)
class PlanFailed:
    pass


@dataclass(frozen=True)
class PlanStalled:
    pass


@dataclass(frozen=True)
class PlanCancelled:
    pass


PreflightResult = Union[PlanReady, PlanRefused, PlanFailed]


def _note_progress(session: ArchiveStreamSession) -> None:
    session.preflight_units += 1
    try:
        session.transport.loop.call_soon_threadsafe(session.preflight_signal.set)
    except RuntimeError:
        pass


def _publish_plan(session: ArchiveStreamSession, result: PreflightResult) -> bool:
    if session.cancel.is_set():
        return False
    session.plan_outcome = result
    try:
        session.transport.loop.call_soon_threadsafe(session.preflight_signal.set)
    except RuntimeError:
        return False
    return True


def resolve_archive_folder(
    date_folder: str,
    sub_folder: SubFolder,
    settings: Settings
) -> Union[Tuple[Literal["ok"], PhotoFileIndex, Path], Tuple[Literal["err"], PlanRefused]]:
    index = resolve_file_index(date_folder, sub_folder, settings, time.monotonic)
    if index.status != PhotoFileListStatus.OK:
        reason = "ListingUnavailable" if index.status in (PhotoFileListStatus.UNAVAILABLE, PhotoFileListStatus.UNCONFIGURED) else "FolderNotFound"
        return "err", PlanRefused(reason, index.status.value)

    folder_res = resolve_photo_folder_path(date_folder, settings)
    if folder_res[0] == "err":
        reason = "ListingUnavailable" if folder_res[1] in ("unavailable", "unconfigured") else "FolderNotFound"
        return "err", PlanRefused(reason, "folder_not_found")

    root = folder_res[1]
    try:
        resolved_root = root.resolve()
        resolved_folder = resolved_root if sub_folder == ROOT else (root / sub_folder).resolve()
        resolved_folder.relative_to(resolved_root)
    except ValueError:
        return "err", PlanRefused("FolderNotFound", "folder_not_found")
    except OSError:
        return "err", PlanRefused("ListingUnavailable", "unavailable")
    return "ok", index, resolved_folder


def run_preflight(session: ArchiveStreamSession) -> Optional[PreflightResult]:
    """Resolves, snapshots, probes, and plans. None when cancelled part-way."""
    settings = session.settings
    if session.cancel.is_set():
        return None
    resolution = resolve_archive_folder(session.date_folder, session.sub_folder, settings)
    _note_progress(session)
    if resolution[0] == "err":
        return resolution[1]
    _, index, folder = resolution

    snapshot = build_snapshot(session.selection, index)
    session.snapshot = snapshot

    probes: List[ProbeResult] = []
    for entry in snapshot.entries:
        if session.cancel.is_set():
            return None
        probes.append(probe_entry(folder, entry))
        _note_progress(session)

    plan = build_archive_plan(snapshot, probes, settings.shipping_photos_max_files_per_folder)
    session.plan = plan
    return PlanReady(plan)


def archive_reader_main(session: ArchiveStreamSession) -> None:
    """The reader thread: preflight, then stream. The only archive code that
    touches the share. Returns its reader slot on every exit."""
    started = time.monotonic()
    try:
        try:
            result = run_preflight(session)
        except Exception:
            logger.exception("Archive preflight failed: session_id=%s", session.session_id)
            result = PlanFailed()
        session.preflight_ms = (time.monotonic() - started) * 1000.0
        if result is None or not _publish_plan(session, result):
            return
        if isinstance(result, PlanReady):
            try:
                stream_admitted_members(session, result.plan)
            except Exception:
                logger.exception("Archive reader failed: session_id=%s", session.session_id)
                publish_terminal(session.transport, StreamAbandoned("ConsumerStalled"))
                release(session, "AbandonedStall")
    finally:
        return_reader_slot(session.lease)


@dataclass(frozen=True)
class PlanDisconnected:
    pass


async def _await_disconnect(receive: Callable[[], Any]) -> None:
    while True:
        message = await receive()
        if message["type"] == "http.disconnect":
            return


async def await_plan(
    session: ArchiveStreamSession,
    receive: Callable[[], Any],
    stall_seconds: float
) -> Union[PlanReady, PlanRefused, PlanFailed, PlanStalled, PlanCancelled, PlanDisconnected]:
    """Waits for the reader's plan, bounding the gap between units of progress.

    Selects on the ASGI receive channel as well. With no response in progress a
    pending receive resumes reading, so a client that leaves is observed within
    one read (Phase 32 §7.3).
    """
    signal = session.preflight_signal
    cancelled = session.transport.cancel_mirror
    disconnect_task = asyncio.ensure_future(_await_disconnect(receive))
    try:
        while True:
            if cancelled.is_set() or session.released:
                return PlanCancelled()
            if session.plan_outcome is not None:
                return session.plan_outcome
            if disconnect_task.done():
                return PlanDisconnected()
            signal.clear()
            signal_task = asyncio.ensure_future(signal.wait())
            cancel_task = asyncio.ensure_future(cancelled.wait())
            try:
                done, _ = await asyncio.wait(
                    [signal_task, cancel_task, disconnect_task],
                    timeout=stall_seconds,
                    return_when=asyncio.FIRST_COMPLETED
                )
            finally:
                signal_task.cancel()
                cancel_task.cancel()
            if not done:
                if session.plan_outcome is not None:
                    continue
                return PlanStalled()
    finally:
        if not disconnect_task.done():
            disconnect_task.cancel()
            try:
                await disconnect_task
            except (asyncio.CancelledError, Exception):
                pass


# ---- Streaming the plan (§10) -----------------------------------------------

def _diverge(session: ArchiveStreamSession, name: FileName, cause: DivergenceCause) -> None:
    invalidate_file_index((session.date_folder, session.sub_folder))
    publish_terminal(session.transport, SourceDiverged(name, cause))


def _abandon(session: ArchiveStreamSession, published: PublishResult) -> None:
    if published == "Stalled":
        publish_terminal(session.transport, StreamAbandoned("ConsumerStalled"))
        release(session, "AbandonedStall")
    else:
        publish_terminal(session.transport, StreamAbandoned("SessionReleased"))


def classify_read_failure(cause: OSError) -> Literal["Vanished", "Unreadable"]:
    if isinstance(cause, FileNotFoundError):
        return "Vanished"
    return "Unreadable"


def stream_admitted_members(session: ArchiveStreamSession, plan: ArchivePlan) -> None:
    """Streams exactly the bytes the plan admitted, or reports that the source moved."""
    settings = session.settings
    chunk_bytes = settings.shipping_photos_archive_read_chunk_bytes
    poll_seconds = settings.shipping_photos_archive_credit_poll_seconds
    stall_seconds = settings.shipping_photos_archive_reader_stall_seconds

    def send(item: ArchiveReadItem) -> PublishResult:
        return publish(session.transport, item, session.cancel, poll_seconds, stall_seconds)

    for index, member in enumerate(plan.members):
        if not isinstance(member.source, AdmittedFile):
            continue
        if session.cancel.is_set():
            _abandon(session, "Cancelled")
            return
        probed = member.source.probed
        name = probed.entry.name
        try:
            with open(probed.resolved, "rb") as handle:
                observed = identity_from_stat(os.fstat(handle.fileno()))
                if not same_identity(probed.identity, observed):
                    _diverge(session, name, divergence_between(probed.identity, observed))
                    return
                published = send(MemberBegin(index))
                if published != "Published":
                    _abandon(session, published)
                    return
                remaining = member.size
                while remaining > 0:
                    if session.cancel.is_set():
                        _abandon(session, "Cancelled")
                        return
                    block = handle.read(min(chunk_bytes, remaining))
                    if not block:
                        _diverge(session, name, "Truncated")
                        return
                    remaining -= len(block)
                    published = send(MemberChunk(block))
                    if published != "Published":
                        _abandon(session, published)
                        return
                if handle.read(1):
                    _diverge(session, name, "Extended")
                    return
                published = send(MemberEnd(index))
                if published != "Published":
                    _abandon(session, published)
                    return
        except OSError as failure:
            _diverge(session, name, classify_read_failure(failure))
            return
    publish_terminal(session.transport, StreamFinished())


def _stop_on(session: ArchiveStreamSession, item: Optional[ArchiveReadItem]) -> None:
    if item is None or isinstance(item, StreamAbandoned):
        return
    if isinstance(item, SourceDiverged):
        session.divergence_cause = item.cause
        record_outcome(session, "SourceChanged")
        return
    record_outcome(session, "FailedFraming")


async def emit_archive(session: ArchiveStreamSession, plan: ArchivePlan) -> AsyncIterator[bytes]:
    """Frames the plan onto the response. Touches no filesystem.

    Records Completed only after the final frame's send has returned, and only
    when every declared byte was sent.
    """
    crcs: List[int] = []
    for index, member in enumerate(plan.members):
        yield encode_record(LocalHeader(member))
        if isinstance(member.source, InlineContent):
            if member.source.content:
                yield member.source.content
            crc = member.source.crc
        else:
            item = await take(session.transport)
            if not (isinstance(item, MemberBegin) and item.index == index):
                _stop_on(session, item)
                return
            crc = 0
            payload_count = 0
            while True:
                item = await take(session.transport)
                if isinstance(item, MemberChunk):
                    payload_count += len(item.payload)
                    if payload_count > member.size:
                        record_outcome(session, "FailedFraming")
                        return
                    crc = zlib.crc32(item.payload, crc)
                    yield item.payload
                    continue
                if isinstance(item, MemberEnd) and item.index == index:
                    break
                _stop_on(session, item)
                return
            if payload_count != member.size:
                record_outcome(session, "FailedFraming")
                return
        crcs.append(crc)
        yield encode_record(DataDescriptor(member, crc))

    directory = bytearray()
    for member, crc in zip(plan.members, crcs):
        directory += encode_record(CentralHeader(member, crc))
    for record in end_records(plan):
        directory += encode_record(record)
    for start in range(0, len(directory), DIRECTORY_FRAME_BYTES):
        yield bytes(directory[start:start + DIRECTORY_FRAME_BYTES])

    if session.bytes_sent == plan.declared_bytes:
        record_outcome(session, "Completed")
    else:
        record_outcome(session, "FailedFraming")


# ---- The response seam (§3) -------------------------------------------------

AsgiSend = Callable[[dict], Any]


async def send_frame(
    session: ArchiveStreamSession,
    send: AsgiSend,
    frame: bytes,
    stall_seconds: float
) -> Literal["Sent", "Refused", "Stalled"]:
    """Hands one frame to the transport only if it fits inside the declared length."""
    if session.plan is None or session.bytes_sent + len(frame) > session.plan.declared_bytes:
        record_outcome(session, "FailedFraming")
        return "Refused"
    with anyio.move_on_after(stall_seconds) as scope:
        await send({"type": "http.response.body", "body": frame, "more_body": True})
    if scope.cancel_called:
        record_outcome(session, "AbandonedDisconnect")
        return "Stalled"
    session.bytes_sent += len(frame)
    return "Sent"


async def finish_response(
    session: ArchiveStreamSession,
    send: AsgiSend,
    stall_seconds: float
) -> Literal["Finished", "Withheld"]:
    """Sends the terminal body message if and only if the archive is whole."""
    if session.outcome != "Completed" or session.plan is None or session.bytes_sent != session.plan.declared_bytes:
        return "Withheld"
    with anyio.move_on_after(stall_seconds):
        await send({"type": "http.response.body", "body": b"", "more_body": False})
    return "Finished"
