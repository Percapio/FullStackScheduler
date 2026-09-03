import threading
import logging
import time
from typing import Set, Deque
from collections import deque
from backend.app.config import Settings
from backend.app.services.photo_files import resolve_file_index, PhotoFileListStatus
from backend.app.services.photo_thumbnails import generate_once

logger = logging.getLogger(__name__)

_warm_lock = threading.Lock()
_warm_queue: Deque[str] = deque()
_warm_known: Set[str] = set()
_warm_thread: threading.Thread | None = None
_warm_stop = threading.Event()

def enqueue_warm(date_folder: str, settings: Settings) -> None:
    if not settings.shipping_photos_thumb_warm_enabled:
        return
        
    global _warm_thread
    
    with _warm_lock:
        if date_folder in _warm_known:
            return
            
        if len(_warm_queue) >= settings.shipping_photos_thumb_warm_queue_max_folders:
            oldest = _warm_queue.popleft()
            _warm_known.discard(oldest)
            
        _warm_queue.append(date_folder)
        _warm_known.add(date_folder)
        
        if _warm_thread is None or not _warm_thread.is_alive():
            _warm_stop.clear()
            _warm_thread = threading.Thread(
                target=warm_worker_loop,
                args=(settings, _warm_stop),
                daemon=True,
                name="PhotoWarmWorker"
            )
            _warm_thread.start()

def warm_worker_loop(settings: Settings, stop: threading.Event) -> None:
    while not stop.is_set():
        with _warm_lock:
            if not _warm_queue:
                break
            date_folder = _warm_queue.popleft()
            
        try:
            _process_folder(date_folder, settings, stop)
        except Exception as e:
            logger.debug("Warm worker caught exception for folder %s: %s", date_folder, e)
        finally:
            with _warm_lock:
                _warm_known.discard(date_folder)
                
def _process_folder(date_folder: str, settings: Settings, stop: threading.Event) -> None:
    clock = time.monotonic
    index = resolve_file_index(date_folder, settings, clock)
    
    if index.status != PhotoFileListStatus.OK:
        logger.debug("Warm worker skipping folder %s due to non-OK index status: %s", date_folder, index.status)
        return
        
    for entry in index.entries:
        if stop.is_set():
            return
            
        if not entry.previewable:
            continue
            
        attempts = 0
        while attempts < settings.shipping_photos_thumb_warm_max_attempts:
            if stop.is_set():
                return
                
            try:
                status, outcome = generate_once(date_folder, entry.name, index, "warm", settings)
            except Exception as e:
                logger.debug("Per-file exception for %s: %s", entry.name, e)
                break
            if status == "err" and outcome == "saturated":
                attempts += 1
                time.sleep(settings.shipping_photos_thumb_warm_backoff_seconds)
                continue
            
            # Whether it succeeded or failed, move to next file
            break
            
def shutdown_warm_worker(timeout: float = 2.0):
    global _warm_thread
    if _warm_thread and _warm_thread.is_alive():
        _warm_stop.set()
        _warm_thread.join(timeout)
