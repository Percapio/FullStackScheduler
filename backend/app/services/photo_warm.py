import threading
import logging
import time
from typing import Set, Deque, Union
from collections import deque
from backend.app.config import Settings
from backend.app.services.photo_files import resolve_file_index, PhotoFileListStatus, FolderKey, ROOT
from backend.app.services.photo_thumbnails import generate_once

logger = logging.getLogger(__name__)

_warm_lock = threading.Lock()
_warm_queue: Deque[FolderKey] = deque()
_warm_known: Set[FolderKey] = set()
_warm_thread: threading.Thread | None = None
_warm_worker_running = False
_warm_stop = threading.Event()

def enqueue_warm(date_folder: str, sub_folder: str, settings: Settings) -> None:
    if not settings.shipping_photos_thumb_warm_enabled:
        return
        
    global _warm_thread, _warm_worker_running
    
    with _warm_lock:
        key = (date_folder, sub_folder)
        if key in _warm_known:
            return
            
        if sub_folder != ROOT:
            date_subfolders = [k for k in _warm_queue if k[0] == date_folder and k[1] != ROOT]
            if len(date_subfolders) >= settings.shipping_photos_thumb_warm_max_subfolders_per_date:
                oldest = date_subfolders[0]
                _warm_queue.remove(oldest)
                _warm_known.discard(oldest)
                
        while len(_warm_queue) >= settings.shipping_photos_thumb_warm_queue_max_keys:
            oldest = _warm_queue.popleft()
            _warm_known.discard(oldest)
            
        _warm_queue.append(key)
        _warm_known.add(key)
        
        # _warm_worker_running -- not is_alive() -- is what decides this.
        # A worker that drains the queue retires by clearing the flag under
        # this same lock, so there is no window in which the queue is empty,
        # the thread is still winding down, and is_alive() reports True. An
        # enqueue landing in that window used to start nothing and strand the
        # folder in _warm_known permanently. is_alive() stays as the backstop
        # for a thread that died without retiring.
        if not _warm_worker_running or _warm_thread is None or not _warm_thread.is_alive():
            _warm_stop.clear()
            _warm_worker_running = True
            _warm_thread = threading.Thread(
                target=warm_worker_loop,
                args=(settings, _warm_stop),
                daemon=True,
                name="PhotoWarmWorker"
            )
            _warm_thread.start()

def warm_worker_loop(settings: Settings, stop: threading.Event) -> None:
    while True:
        key = _next_folder_or_retire(stop)
        if key is None:
            return

        try:
            _process_folder(key, settings, stop)
        except Exception as e:
            logger.debug("Warm worker caught exception for folder %s: %s", key, e)
        finally:
            with _warm_lock:
                _warm_known.discard(key)

def _next_folder_or_retire(stop: threading.Event) -> Union[FolderKey, None]:
    """Pops the next folder, or retires this worker.

    Both outcomes are decided in one critical section so that observing an
    empty queue and standing down are atomic with respect to enqueue_warm.
    """
    global _warm_worker_running

    with _warm_lock:
        if not stop.is_set() and _warm_queue:
            return _warm_queue.popleft()
        if _warm_thread is threading.current_thread():
            _warm_worker_running = False
        return None

def _process_folder(key: FolderKey, settings: Settings, stop: threading.Event) -> None:
    clock = time.monotonic
    date_folder, sub_folder = key
    index = resolve_file_index(date_folder, sub_folder, settings, clock)
    
    if index.status != PhotoFileListStatus.OK:
        logger.debug("Warm worker skipping folder %s due to non-OK index status: %s", key, index.status)
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
                status, outcome = generate_once(date_folder, sub_folder, entry.name, index, "warm", settings)
            except Exception as e:
                logger.debug("Per-file exception for %s: %s", entry.name, e)
                break
            if status == "err" and outcome == "saturated":
                attempts += 1
                # stop.wait, not time.sleep: shutdown must not be held up for
                # backoff x attempts on whatever file the worker is retrying.
                if stop.wait(settings.shipping_photos_thumb_warm_backoff_seconds):
                    return
                continue
            
            # Whether it succeeded or failed, move to next file
            break
            
def shutdown_warm_worker(timeout: float = 2.0):
    global _warm_thread, _warm_worker_running
    if _warm_thread and _warm_thread.is_alive():
        _warm_stop.set()
        _warm_thread.join(timeout)
    with _warm_lock:
        _warm_worker_running = False
