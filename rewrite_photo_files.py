import re
from pathlib import Path

path = Path(r"d:\Dev\Scheduler\Schedule\backend\app\services\photo_files.py")
content = path.read_text(encoding="utf-8")

match = re.search(r'import queue\s*ReadFailure = .*', content, re.DOTALL)
if match:
    prefix = content[:match.start()]
else:
    idx = content.find("import queue")
    prefix = content[:idx]

new_content = prefix + """
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
    unreadable = 0 
    vanished = 0
    logger.log(level, "ArchiveSessionRecord: session_id=%s date_folder=%s sub_folder=%s entry_count=%d unresolved_count=%d snapshot_bytes=%d bytes_sent=%d permits_in_use=%d live_readers=%d duration_ms=%.1f outcome=%s",
        session.session_id, session.date_folder, session.sub_folder, len(session.snapshot.entries), len(session.snapshot.unresolved), snapshot_bytes, session.bytes_sent, session.lease.permits.in_use, session.lease.permits.live_readers, duration_ms, outcome
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
            
            if missing_files_vanished or missing_files_unreadable:
                zinfo = zipfile.ZipInfo(filename="_MISSING.txt")
                missing_name = "_MISSING.txt"
                target_names = [e.name for e in snapshot.entries] + snapshot.unresolved
                if missing_name in target_names:
                    missing_name = f"_MISSING_{time.time_ns()}.txt"
                    zinfo.filename = missing_name
                
                lines = ["The following files are missing or incomplete in this archive:"]
                for n in missing_files_vanished:
                    lines.append(f"  {n} — moved or deleted before the archive was built")
                for n in missing_files_unreadable:
                    lines.append(f"  {n} — could not be read from the photos folder")
                    
                zf.writestr(zinfo, "\n".join(lines))
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
"""

path.write_text(new_content, encoding="utf-8")
print("Done")
