import os
import time
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Union
from PIL import Image, ImageOps

from ..config import Settings, _runtime_root
from .photo_files import resolve_photo_file_path, PhotoFileIndex
import threading
from contextlib import contextmanager

logger = logging.getLogger(__name__)

GateRejection = Literal["saturated", "timeout"]
Priority = Literal["interactive", "warm"]

_gate_lock = threading.Lock()
_gate_cv = threading.Condition(_gate_lock)
_gate_active = 0
_gate_waiting = 0

@contextmanager
def acquire_thumbnail_permit(priority: Priority, settings: Settings):
    """Acquires a generation permit.

    INTERACTIVE blocks for at most queue_wait_seconds, and is rejected without
    queueing once max_waiters are already waiting. WARM never blocks and never
    registers as a waiter, so the worker cannot occupy a slot an operator is
    waiting for nor lengthen the queue by standing in it.

    The rejection is decided under _gate_cv but yielded outside it: a caller
    that did any work in its error branch would otherwise hold the gate's lock
    for the duration and stall every other request.
    """
    global _gate_active, _gate_waiting

    rejection: GateRejection | None = None

    with _gate_cv:
        if _gate_active >= settings.shipping_photos_thumb_max_concurrent:
            if priority == "warm":
                rejection = "saturated"
            elif _gate_waiting >= settings.shipping_photos_thumb_max_waiters:
                rejection = "saturated"
            else:
                _gate_waiting += 1
                try:
                    granted = _gate_cv.wait_for(
                        lambda: _gate_active < settings.shipping_photos_thumb_max_concurrent,
                        settings.shipping_photos_thumb_queue_wait_seconds
                    )
                finally:
                    _gate_waiting -= 1
                if not granted:
                    rejection = "timeout"

        if rejection is None:
            _gate_active += 1

    if rejection is not None:
        yield "err", rejection
        return

    try:
        yield "ok", None
    finally:
        with _gate_cv:
            _gate_active -= 1
            _gate_cv.notify()



ThumbnailFailure = Literal[
    "unconfigured",
    "unavailable",
    "folder_not_found",
    "file_not_found",
    "not_a_file",
    "not_previewable",
    "cache_unavailable"
]

@dataclass
class ThumbnailResult:
    path: Path
    media_type: str

def _get_cache_dir() -> Path:
    d = _runtime_root() / "outputs" / "thumbnails"
    d.mkdir(parents=True, exist_ok=True)
    return d

def _clean_cache_if_needed(cache_dir: Path, settings: Settings) -> None:
    try:
        entries = []
        total_size = 0
        for e in os.scandir(cache_dir):
            if e.is_file():
                st = e.stat()
                total_size += st.st_size
                entries.append((e.path, st.st_mtime, st.st_size))
                
        if total_size <= settings.shipping_photos_thumb_cache_max_bytes:
            return
            
        entries.sort(key=lambda x: x[1]) # oldest first
        
        for path, _, size in entries:
            try:
                os.remove(path)
                total_size -= size
                if total_size <= settings.shipping_photos_thumb_cache_max_bytes:
                    break
            except OSError:
                pass
    except OSError:
        pass

_write_counter = 0

@dataclass
class InflightGeneration:
    done: threading.Event
    outcome: Union[tuple[Literal["ok"], ThumbnailResult], tuple[Literal["err"], ThumbnailFailure], None] = None

_inflight_lock = threading.Lock()
_inflight: dict[str, InflightGeneration] = {}

def thumbnail_cache_key(date_folder: str, sub_folder: str, file_name: str, version: str) -> str:
    import hashlib
    raw = f"{date_folder}\x00{sub_folder}\x00{file_name}\x00{version}".encode('utf-8')
    return hashlib.sha256(raw).hexdigest()[:32] + ".webp"

def generate_once(
    date_folder: str,
    sub_folder: str,
    file_name: str,
    index: PhotoFileIndex,
    priority: Priority,
    settings: Settings
) -> Union[tuple[Literal["ok"], ThumbnailResult], tuple[Literal["err"], Union[ThumbnailFailure, GateRejection]]]:
    
    # 1. Resolve source
    res = resolve_photo_file_path(date_folder, sub_folder, file_name, index, settings)
    if res[0] == "err":
        return res
    source_path = res[1]
    
    entry = index.by_name.get(file_name)
    if not entry or not entry.previewable:
        return "err", "not_previewable"
        
    cache_key = thumbnail_cache_key(date_folder, sub_folder, file_name, entry.version)
    
    cache_dir = _get_cache_dir()
    cache_path = cache_dir / cache_key
    
    def check_cache():
        try:
            st = cache_path.stat()
            if st.st_size == 0:
                return "err", "not_previewable"
            now = time.time()
            os.utime(cache_path, (now, now))
            return "ok", ThumbnailResult(path=cache_path, media_type="image/webp")
        except OSError:
            return None
            
    deadline = time.monotonic() + settings.shipping_photos_thumb_queue_wait_seconds

    while True:
        # 1. Cache hit? No lock, no permit.
        hit = check_cache()
        if hit:
            return hit

        # 2. Is this key already being generated?
        with _inflight_lock:
            record = _inflight.get(cache_key)

        # 3./4. Wait on the generator's outcome WITHOUT holding a permit. A
        #       waiter that held one while waiting on the holder of another is
        #       how N permits deadlock on N distinct photos.
        if record is not None:
            remaining = deadline - time.monotonic()
            if remaining > 0:
                record.done.wait(timeout=remaining)
                if record.outcome is not None:
                    return record.outcome
            return "err", "timeout"

        # 5. No generator yet. Take a permit and try to become one.
        record = None
        with acquire_thumbnail_permit(priority, settings) as (status, reason):
            if status == "err":
                return "err", reason

            # 6. The cache may have been populated while we queued for a permit.
            hit = check_cache()
            if hit:
                return hit

            # 7. So may the in-flight map. Two decoders on one key is what this
            #    function exists to prevent, so claim the key or don't generate.
            with _inflight_lock:
                if cache_key not in _inflight:
                    record = InflightGeneration(done=threading.Event())
                    _inflight[cache_key] = record

            if record is None:
                # Someone claimed it while we queued. `continue` leaves this
                # `with`, so the permit is released before we re-join them on
                # the next pass with whatever budget is left. Returning a
                # rejection here instead would 503 a caller that has not yet
                # waited at all -- and with the client retry gone, that tile
                # is a permanent placeholder.
                continue

            # 8. Generate, then publish the outcome and retire the record in a
            #    real finally: an exception between registration and completion
            #    would otherwise park every subsequent caller for the full wait,
            #    turning one corrupt photo into a stall for the process's life.
            outcome = ("err", "not_previewable")
            try:
                outcome = _generate_impl(source_path, cache_path, cache_dir, settings)
            except Exception as e:
                logger.error("Unexpected error generating %s: %s", cache_key, e)
            finally:
                with _inflight_lock:
                    record.outcome = outcome
                    _inflight.pop(cache_key, None)
                    record.done.set()

            return outcome

def _generate_impl(source_path: Path, cache_path: Path, cache_dir: Path, settings: Settings):
    global _write_counter
    max_edge = settings.shipping_photos_thumb_max_edge_px
    quality = settings.shipping_photos_thumb_quality
    
    temp_path = cache_path.with_suffix(f".tmp{os.getpid()}{time.time_ns()}")
    
    try:
        with Image.open(source_path) as img:
            # Transpose AFTER the resize, against Patch 05 5.1's stated order.
            # exif_transpose forces a full load, which discards the DCT-scaled
            # decode thumbnail() gets from draft() -- 50 ms against 16 ms on a
            # 4032x3024 orientation-6 JPEG, on the one optimisation 0.1's whole
            # cost model rests on. The fit box is square, so both orders give
            # pixel-identical output; only the cost differs.
            img.thumbnail((max_edge, max_edge), Image.Resampling.LANCZOS)
            img = ImageOps.exif_transpose(img)
            img.save(temp_path, format="WEBP", quality=quality)
    except Exception as e:
        logger.info("Thumbnail generation failed for %s: %s", source_path, e)
        try:
            temp_path.write_bytes(b"")
            temp_path.replace(cache_path)
            _write_counter += 1
            if _write_counter >= settings.shipping_photos_thumb_sweep_every_n_writes:
                _write_counter = 0
                _clean_cache_if_needed(cache_dir, settings)
        except OSError:
            pass 
            
        return "err", "not_previewable"
        
    try:
        temp_path.replace(cache_path)
        _write_counter += 1
        if _write_counter >= settings.shipping_photos_thumb_sweep_every_n_writes:
            _write_counter = 0
            _clean_cache_if_needed(cache_dir, settings)
    except OSError as e:
        logger.warning("Failed to commit thumbnail cache %s: %s", cache_path, e)
        try:
            temp_path.unlink()
        except OSError:
            pass
        return "err", "cache_unavailable"
        
    return "ok", ThumbnailResult(path=cache_path, media_type="image/webp")
