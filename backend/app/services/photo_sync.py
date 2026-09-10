import os
import json
import logging
import stat
import threading
import time
import secrets
from datetime import datetime, timedelta, date, time as datetime_time
from pathlib import Path
from typing import TypedDict, List, Optional, Tuple, Callable, Literal

from ..config import Settings, _runtime_root
from .runtime_config import load_runtime_config, effective_photos_dir
from .photo_files import is_plausible_file_name, is_plausible_folder_name, invalidate_file_index, ALL_FOLDERS
from .shipping_photos import invalidate_index, PHOTO_FOLDER_PATTERN

logger = logging.getLogger(__name__)

SyncOutcome = Literal["Ok", "Partial", "Failed"]
SyncErrorKind = Literal["None", "SourceUnavailable", "DestinationUnavailable", "DestinationConflict", "Storage", "Cancelled"]

class PhotoSyncState(TypedDict):
    last_completed_date: str | None
    last_run_started_at: str | None
    last_run_finished_at: str | None
    last_run_outcome: SyncOutcome | None
    last_run_files_copied: int
    last_run_bytes_copied: int
    last_run_files_deferred: int
    last_run_files_failed: int
    last_run_dates_skipped: List[str]
    last_error_kind: SyncErrorKind

AdmissionVerdict = Literal[
    "Admit",
    "SkipUnchanged",
    "DeferQuiet",
    "RejectName",
    "RejectContainment",
    "RejectDepth",
    "RejectLink",
    "RejectTempArtifact"
]

class SyncCandidate(TypedDict):
    source_path: Path
    destination_path: Path
    size_bytes: int
    mtime_ns: int

StopReason = Literal[
    "Completed",
    "FileCapReached",
    "ByteCapReached",
    "DeadlineReached",
    "SourceUnavailable",
    "DestinationUnavailable",
    "DestinationConflict",
    "Cancelled"
]

class SyncRunOutcome(TypedDict):
    started_at: str
    finished_at: str
    dates_scanned: List[str]
    dates_skipped: List[str]
    files_copied: int
    bytes_copied: int
    files_deferred: int
    files_failed: int
    stop_reason: StopReason

def _state_file_path() -> Path:
    return _runtime_root() / "photo-sync-state.json"

def get_photo_sync_state() -> PhotoSyncState:
    path = _state_file_path()
    default_state: PhotoSyncState = {
        "last_completed_date": None,
        "last_run_started_at": None,
        "last_run_finished_at": None,
        "last_run_outcome": None,
        "last_run_files_copied": 0,
        "last_run_bytes_copied": 0,
        "last_run_files_deferred": 0,
        "last_run_files_failed": 0,
        "last_run_dates_skipped": [],
        "last_error_kind": "None"
    }
    if not path.exists():
        return default_state
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
            for k in default_state:
                if k in data:
                    default_state[k] = data[k]
        return default_state
    except Exception:
        return default_state

def _save_photo_sync_state(state: PhotoSyncState) -> None:
    path = _state_file_path()
    temp_path = path.with_suffix(".json.tmp")
    try:
        with open(temp_path, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp_path, path)
    except Exception as e:
        logger.error(f"Failed to save sync state: {e}")
        if temp_path.exists():
            try:
                temp_path.unlink()
            except Exception:
                pass

def scheduled_run_is_due(last_completed_date: str | None, scheduled_time: datetime_time, now: datetime) -> bool:
    if last_completed_date is not None:
        if now.date().strftime("%Y_%m_%d") <= last_completed_date:
            return False
            
    if now.time() < scheduled_time:
        return False
        
    return True

def sync_date_window(last_completed_date: str | None, today: date, settings: Settings) -> Tuple[List[str], List[str]]:
    lookback = settings.shipping_photos_auto_copy_lookback_days
    lookback_max = settings.shipping_photos_auto_copy_lookback_max_days
    
    start_date = today - timedelta(days=lookback - 1)
    
    if last_completed_date:
        try:
            last_date = datetime.strptime(last_completed_date, "%Y_%m_%d").date()
            if last_date < start_date:
                start_date = last_date + timedelta(days=1)
        except ValueError:
            pass

    max_start_date = today - timedelta(days=lookback_max - 1)
    not_scanned = []
    
    if start_date < max_start_date:
        curr = start_date
        while curr < max_start_date:
            not_scanned.append(curr.strftime("%Y_%m_%d"))
            curr += timedelta(days=1)
        start_date = max_start_date
        
    scanned = []
    curr = today
    while curr >= start_date:
        scanned.append(curr.strftime("%Y_%m_%d"))
        curr -= timedelta(days=1)
        
    if not_scanned:
        logger.warning(f"Sync skipped dates older than max lookback ({lookback_max} days): {not_scanned}")
        
    return scanned, not_scanned

TEMP_PREFIX: str = ".synctmp-"

def classify_source_entry(
    source_entry: os.DirEntry,
    destination_path: Path,
    destination_root: Path,
    depth: int,
    now_ns: int,
    settings: Settings
) -> AdmissionVerdict:
    if source_entry.name.startswith(TEMP_PREFIX):
        return "RejectTempArtifact"
        
    if source_entry.is_symlink():
        return "RejectLink"
        
    is_dir = source_entry.is_dir(follow_symlinks=False)
    is_file = source_entry.is_file(follow_symlinks=False)
    
    if is_dir:
        if depth >= settings.shipping_photos_auto_copy_max_depth:
            return "RejectDepth"
        if not is_plausible_folder_name(source_entry.name):
            return "RejectName"
    elif is_file:
        if not is_plausible_file_name(source_entry.name):
            return "RejectName"
    else:
        return "RejectName"

    try:
        resolved_dest = destination_path.resolve()
        resolved_root = destination_root.resolve()
        resolved_dest.relative_to(resolved_root)
    except (ValueError, OSError):
        return "RejectContainment"

    try:
        st = source_entry.stat(follow_symlinks=False)
    except OSError:
        return "RejectLink"
        
    mtime_ns = st.st_mtime_ns
    quiet_ns = int(settings.shipping_photos_auto_copy_quiet_seconds * 1e9)
    if is_file and (now_ns - mtime_ns) < quiet_ns:
        return "DeferQuiet"

    if is_file and destination_path.exists():
        try:
            dst_st = destination_path.stat()
            if dst_st.st_size == st.st_size and dst_st.st_mtime_ns == mtime_ns:
                return "SkipUnchanged"
        except OSError:
            pass

    return "Admit"

def copy_one(
    candidate: SyncCandidate,
    stop: threading.Event,
    deadline: float,
    settings: Settings
) -> Tuple[Literal["ok"], int] | Tuple[Literal["err"], SyncErrorKind]:
    tmp_name = f"{TEMP_PREFIX}{secrets.token_hex(8)}"
    tmp_path = candidate["destination_path"].parent / tmp_name
    
    try:
        tmp_path.parent.mkdir(parents=True, exist_ok=True)
    except OSError:
        return "err", "DestinationUnavailable"

    bytes_copied = 0
    chunk_size = settings.shipping_photos_auto_copy_copy_chunk_bytes
    
    try:
        if time.monotonic() >= deadline:
            return "err", "Cancelled"
            
        with open(candidate["source_path"], "rb") as src, open(tmp_path, "wb") as dst:
            while True:
                if stop.is_set() or time.monotonic() >= deadline:
                    break
                chunk = src.read(chunk_size)
                if not chunk:
                    break
                dst.write(chunk)
                bytes_copied += len(chunk)
                
        if stop.is_set() or time.monotonic() >= deadline:
            try:
                tmp_path.unlink()
            except OSError as e:
                logger.warning(f"Failed to remove temp file after cancellation {tmp_path}: {e}")
            return "err", "Cancelled"

        os.replace(tmp_path, candidate["destination_path"])
        os.utime(candidate["destination_path"], ns=(candidate["mtime_ns"], candidate["mtime_ns"]))
        return "ok", bytes_copied
        
    except OSError as e:
        if e.errno == 28: # ENOSPC
            kind = "Storage"
        else:
            kind = "DestinationUnavailable"
            
        if tmp_path.exists():
            try:
                tmp_path.unlink()
            except OSError as ex:
                logger.warning(f"Failed to remove temp file after error {tmp_path}: {ex}")
        return "err", kind

def run_photo_sync(
    settings: Settings,
    stop: threading.Event,
    clock: Callable[[], float]
) -> SyncRunOutcome:
    started_at = datetime.now().isoformat()
    now_ts = clock()
    deadline = now_ts + settings.shipping_photos_auto_copy_run_deadline_seconds
    
    outcome: SyncRunOutcome = {
        "started_at": started_at,
        "finished_at": "",
        "dates_scanned": [],
        "dates_skipped": [],
        "files_copied": 0,
        "bytes_copied": 0,
        "files_deferred": 0,
        "files_failed": 0,
        "stop_reason": "Completed"
    }

    config = load_runtime_config()
    source_dir = config.get("shipping_photos_auto_copy_source")
    if not source_dir:
        outcome["stop_reason"] = "SourceUnavailable"
        outcome["finished_at"] = datetime.now().isoformat()
        return outcome

    dest_dir, _ = effective_photos_dir(settings)
    if not dest_dir:
        outcome["stop_reason"] = "DestinationUnavailable"
        outcome["finished_at"] = datetime.now().isoformat()
        return outcome

    source_root = Path(source_dir)
    dest_root = Path(dest_dir)

    try:
        resolved_src = source_root.resolve()
        resolved_dst = dest_root.resolve()
        try:
            resolved_src.relative_to(resolved_dst)
            conflict = True
        except ValueError:
            try:
                resolved_dst.relative_to(resolved_src)
                conflict = True
            except ValueError:
                conflict = False
        if conflict:
            outcome["stop_reason"] = "DestinationConflict"
            outcome["finished_at"] = datetime.now().isoformat()
            return outcome
    except OSError:
        pass

    if not source_root.is_dir():
        outcome["stop_reason"] = "SourceUnavailable"
        outcome["finished_at"] = datetime.now().isoformat()
        return outcome
        
    if not dest_root.is_dir():
        outcome["stop_reason"] = "DestinationUnavailable"
        outcome["finished_at"] = datetime.now().isoformat()
        return outcome

    state = get_photo_sync_state()
    scanned, skipped = sync_date_window(state["last_completed_date"], datetime.now().date(), settings)
    outcome["dates_scanned"] = scanned
    outcome["dates_skipped"] = skipped
    
    new_dates_created = False
    now_ns = time.time_ns()
    
    def sweep_orphans(folder_path: Path):
        try:
            for entry in os.scandir(folder_path):
                if entry.name.startswith(TEMP_PREFIX) and entry.is_file(follow_symlinks=False):
                    mtime_ns = entry.stat(follow_symlinks=False).st_mtime_ns
                    age_seconds = (now_ns - mtime_ns) / 1e9
                    if age_seconds > settings.shipping_photos_auto_copy_temp_reap_seconds:
                        try:
                            os.remove(entry.path)
                            logger.info(f"Swept orphaned temp file: {entry.path}")
                        except OSError as e:
                            logger.warning(f"Failed to sweep orphan {entry.path}: {e}")
        except OSError:
            pass

    for date_folder in scanned:
        if outcome["stop_reason"] != "Completed":
            break

        if stop.is_set():
            outcome["stop_reason"] = "Cancelled"
            break
            
        current_config = load_runtime_config()
        if not current_config.get("shipping_photos_auto_copy_enabled"):
            outcome["stop_reason"] = "Cancelled"
            break
            
        if clock() >= deadline:
            outcome["stop_reason"] = "DeadlineReached"
            break

        src_date_path = source_root / date_folder
        if not src_date_path.is_dir():
            continue
            
        dst_date_path = dest_root / date_folder
        if dst_date_path.exists():
            sweep_orphans(dst_date_path)
        
        date_folder_copied_something = False

        def walk(src_path: Path, dst_path: Path, depth: int):
            if outcome["stop_reason"] != "Completed" or stop.is_set():
                return
            
            if clock() >= deadline:
                outcome["stop_reason"] = "DeadlineReached"
                return

            try:
                iterator = os.scandir(src_path)
            except OSError:
                return

            with iterator:
                for entry in iterator:
                    if stop.is_set():
                        outcome["stop_reason"] = "Cancelled"
                        return
                    
                    current_config = load_runtime_config()
                    if not current_config.get("shipping_photos_auto_copy_enabled"):
                        outcome["stop_reason"] = "Cancelled"
                        return
                        
                    verdict = classify_source_entry(entry, dst_path / entry.name, dest_root, depth, now_ns, settings)
                    
                    if verdict == "RejectDepth" or verdict == "RejectName" or verdict == "RejectContainment" or verdict == "RejectLink" or verdict == "RejectTempArtifact":
                        continue
                    elif verdict == "SkipUnchanged":
                        continue
                    elif verdict == "DeferQuiet":
                        outcome["files_deferred"] += 1
                        continue
                    elif verdict == "Admit":
                        if entry.is_dir(follow_symlinks=False):
                            walk(Path(entry.path), dst_path / entry.name, depth + 1)
                        else:
                            if outcome["files_copied"] >= settings.shipping_photos_auto_copy_max_files_per_run:
                                outcome["stop_reason"] = "FileCapReached"
                                return
                            if outcome["bytes_copied"] >= settings.shipping_photos_auto_copy_max_bytes_per_run:
                                outcome["stop_reason"] = "ByteCapReached"
                                return
                                
                            try:
                                candidate: SyncCandidate = {
                                    "source_path": Path(entry.path),
                                    "destination_path": dst_path / entry.name,
                                    "size_bytes": entry.stat().st_size,
                                    "mtime_ns": entry.stat().st_mtime_ns
                                }
                            except OSError:
                                outcome["files_failed"] += 1
                                continue
                                
                            nonlocal date_folder_copied_something, new_dates_created
                            if not dst_date_path.exists():
                                new_dates_created = True
                            
                            res, val = copy_one(candidate, stop, deadline, settings)
                            if res == "ok":
                                outcome["files_copied"] += 1
                                outcome["bytes_copied"] += val
                                date_folder_copied_something = True
                            else:
                                if val == "Cancelled":
                                    outcome["stop_reason"] = "Cancelled"
                                    return
                                elif val in ("DestinationUnavailable", "Storage"):
                                    outcome["stop_reason"] = val
                                    return
                                else:
                                    outcome["files_failed"] += 1

        walk(src_date_path, dst_date_path, 0)
        
        if date_folder_copied_something:
            invalidate_file_index(date_folder)

    if new_dates_created:
        invalidate_index()

    outcome["finished_at"] = datetime.now().isoformat()
    return outcome

_worker_lock = threading.Lock()
_worker_running = False

def get_worker_running() -> bool:
    with _worker_lock:
        return _worker_running

def _run_photo_sync_locked(settings: Settings, stop: threading.Event, clock: Callable[[], float]) -> bool:
    """Returns True if the worker ran, False if it was already running"""
    global _worker_running
    with _worker_lock:
        if _worker_running:
            return False
        _worker_running = True
        
    try:
        outcome = run_photo_sync(settings, stop, clock)
        
        state = get_photo_sync_state()
        new_state: PhotoSyncState = dict(state) # type: ignore
        new_state["last_run_started_at"] = outcome["started_at"]
        new_state["last_run_finished_at"] = outcome["finished_at"]
        new_state["last_run_files_copied"] = outcome["files_copied"]
        new_state["last_run_bytes_copied"] = outcome["bytes_copied"]
        new_state["last_run_files_deferred"] = outcome["files_deferred"]
        new_state["last_run_files_failed"] = outcome["files_failed"]
        new_state["last_run_dates_skipped"] = outcome["dates_skipped"]
        
        if outcome["stop_reason"] == "Completed":
            if outcome["files_failed"] > 0:
                new_state["last_run_outcome"] = "Partial"
                new_state["last_error_kind"] = "None"
            else:
                new_state["last_run_outcome"] = "Ok"
                new_state["last_error_kind"] = "None"
                new_state["last_completed_date"] = datetime.now().date().strftime("%Y_%m_%d")
        else:
            if outcome["stop_reason"] in ("FileCapReached", "ByteCapReached", "DeadlineReached"):
                new_state["last_run_outcome"] = "Partial"
                new_state["last_error_kind"] = "None"
            elif outcome["stop_reason"] == "Cancelled":
                new_state["last_run_outcome"] = "Failed"
                new_state["last_error_kind"] = "Cancelled"
            else:
                new_state["last_run_outcome"] = "Failed"
                new_state["last_error_kind"] = outcome["stop_reason"] # type: ignore
                
        _save_photo_sync_state(new_state)
    finally:
        with _worker_lock:
            _worker_running = False
            
    return True

def photo_sync_worker_loop(settings: Settings, stop: threading.Event) -> None:
    logger.info("Photo sync worker thread started")
    
    while not stop.wait(settings.shipping_photos_auto_copy_tick_seconds):
        config = load_runtime_config()
        if not config.get("shipping_photos_auto_copy_enabled"):
            continue
            
        sync_time_str = config.get("shipping_photos_auto_copy_time")
        if not sync_time_str:
            continue
            
        try:
            h, m = map(int, sync_time_str.split(":"))
            scheduled_time = datetime_time(hour=h, minute=m)
        except ValueError:
            continue
            
        state = get_photo_sync_state()
        
        if scheduled_run_is_due(state["last_completed_date"], scheduled_time, datetime.now()):
            _run_photo_sync_locked(settings, stop, time.monotonic)

    logger.info("Photo sync worker thread stopped")
