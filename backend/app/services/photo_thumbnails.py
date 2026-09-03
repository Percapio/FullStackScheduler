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
    global _gate_active, _gate_waiting
    
    with _gate_cv:
        if _gate_active >= settings.shipping_photos_thumb_max_concurrent:
            if priority == "warm":
                yield "err", "saturated"
                return
            if _gate_waiting >= settings.shipping_photos_thumb_max_waiters:
                yield "err", "saturated"
                return
                
            _gate_waiting += 1
            try:
                success = _gate_cv.wait_for(
                    lambda: _gate_active < settings.shipping_photos_thumb_max_concurrent,
                    settings.shipping_photos_thumb_queue_wait_seconds
                )
                if not success:
                    yield "err", "timeout"
                    return
            finally:
                _gate_waiting -= 1
                
        _gate_active += 1
        
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

def resolve_thumbnail(
    date_folder: str,
    file_name: str,
    index: PhotoFileIndex,
    settings: Settings
) -> Union[tuple[Literal["ok"], ThumbnailResult], tuple[Literal["err"], ThumbnailFailure]]:
    global _write_counter
    
    # 1. Resolve source
    res = resolve_photo_file_path(date_folder, file_name, index, settings)
    if res[0] == "err":
        return res
    source_path = res[1]
    
    entry = index.by_name.get(file_name)
    if not entry or not entry.previewable:
        return "err", "not_previewable"
        
    cache_key = f"{date_folder}_{file_name}_{entry.version}.webp"
    
    cache_key = cache_key.replace("\\", "_").replace("/", "_")
    
    cache_dir = _get_cache_dir()
    cache_path = cache_dir / cache_key
    
    # 2. Check cache
    try:
        st = cache_path.stat()
        if st.st_size == 0:
            return "err", "not_previewable"
            
        now = time.time()
        os.utime(cache_path, (now, now))
        return "ok", ThumbnailResult(path=cache_path, media_type="image/webp")
    except OSError:
        pass 
        
    # 3. Generate
    max_edge = settings.shipping_photos_thumb_max_edge_px
    quality = settings.shipping_photos_thumb_quality
    
    temp_path = cache_path.with_suffix(f".tmp{os.getpid()}{time.time_ns()}")
    
    try:
        with Image.open(source_path) as img:
            img = ImageOps.exif_transpose(img)
            img.thumbnail((max_edge, max_edge), Image.Resampling.LANCZOS)
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
