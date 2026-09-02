import os
import re
import threading
import logging
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Callable, Literal, TypeVar, Set

from ..config import Settings
from .runtime_config import effective_photos_dir

logger = logging.getLogger(__name__)

PHOTO_FOLDER_PATTERN = re.compile(r"^[0-9]{4}_[0-9]{2}_[0-9]{2}$")

def is_photo_folder_name(candidate: str) -> bool:
    """True iff `candidate` is a well-formed photo folder name.
    ASCII digits only. Says nothing about calendar validity or existence.
    """
    return bool(PHOTO_FOLDER_PATTERN.fullmatch(candidate))

class PhotoDirectoryStatus(str, Enum):
    UNCONFIGURED = "unconfigured"
    UNAVAILABLE = "unavailable"
    OK = "ok"

@dataclass
class PhotoFolderIndex:
    status: PhotoDirectoryStatus
    folder_names: Set[str]
    scanned_at: float
    truncated: bool
    generation: int

@dataclass
class RateLimited:
    remaining_seconds: float
    kind: Literal["rate_limited"] = "rate_limited"

PhotoOpenFailure = Literal[
    "unconfigured",
    "unavailable",
    "invalid_name",
    "not_found",
    "shell_error"
] | RateLimited

T = TypeVar("T")
E = TypeVar("E")
Result = tuple[Literal["ok"], T] | tuple[Literal["err"], E]

_index_lock = threading.Lock()
_cached_index: PhotoFolderIndex | None = None
_generation_counter: int = 0

def invalidate_index() -> None:
    global _cached_index
    with _index_lock:
        _cached_index = None

def resolve_folder_index(
    settings: Settings,
    clock: Callable[[], float]
) -> PhotoFolderIndex:
    global _cached_index
    global _generation_counter
    now = clock()
    
    dir_path, _ = effective_photos_dir(settings)

    if not dir_path:
        with _index_lock:
            _generation_counter += 1
            gen = _generation_counter
        return PhotoFolderIndex(
            status=PhotoDirectoryStatus.UNCONFIGURED,
            folder_names=set(),
            scanned_at=now,
            truncated=False,
            generation=gen
        )

    with _index_lock:
        if _cached_index is not None:
            age = now - _cached_index.scanned_at
            if _cached_index.status == PhotoDirectoryStatus.OK:
                if age < settings.shipping_photos_index_ttl_seconds:
                    return _cached_index
            elif _cached_index.status == PhotoDirectoryStatus.UNAVAILABLE:
                if age < settings.shipping_photos_unavailable_ttl_seconds:
                    return _cached_index

        _generation_counter += 1
        generation = _generation_counter
        base_path = Path(dir_path)

        try:
            iterator = os.scandir(base_path)
        except OSError:
            new_index = PhotoFolderIndex(
                status=PhotoDirectoryStatus.UNAVAILABLE,
                folder_names=set(),
                scanned_at=now,
                truncated=False,
                generation=generation
            )
            _cached_index = new_index
            return new_index

        folders = set()
        truncated = False
        with iterator:
            for entry in iterator:
                if entry.is_dir():
                    name = entry.name
                    if is_photo_folder_name(name):
                        if len(folders) >= settings.shipping_photos_max_folders:
                            logger.warning("Shipping photos index truncated at %d entries", settings.shipping_photos_max_folders)
                            truncated = True
                            break
                        folders.add(name)

        new_index = PhotoFolderIndex(
            status=PhotoDirectoryStatus.OK,
            folder_names=folders,
            scanned_at=now,
            truncated=truncated,
            generation=generation
        )
        _cached_index = new_index
        return new_index

def update_index(
    observed_generation: int,
    mutate: Callable[[PhotoFolderIndex], PhotoFolderIndex]
) -> bool:
    global _cached_index
    with _index_lock:
        if _cached_index is None or _cached_index.generation != observed_generation:
            return False
        _cached_index = mutate(_cached_index)
        return True

def probe_missing_folders(
    requested: Set[str],
    index: PhotoFolderIndex,
    settings: Settings
) -> Set[str]:
    if index.status != PhotoDirectoryStatus.OK:
        return set()

    missing = {req for req in requested if is_photo_folder_name(req) and req not in index.folder_names}
    if not missing:
        return index.folder_names

    dir_path, _ = effective_photos_dir(settings)
    if not dir_path:
        return index.folder_names
        
    base_path = Path(dir_path)
    hits = set()
    probes_done = 0

    for name in missing:
        if probes_done >= settings.shipping_photos_probe_max:
            break
        probes_done += 1
        try:
            if (base_path / name).is_dir():
                hits.add(name)
        except OSError:
            pass

    if hits:
        def merge(current: PhotoFolderIndex) -> PhotoFolderIndex:
            new_folders = set(current.folder_names)
            truncated = current.truncated
            for hit in hits:
                if len(new_folders) < settings.shipping_photos_max_folders:
                    new_folders.add(hit)
                else:
                    truncated = True
            
            return PhotoFolderIndex(
                status=current.status,
                folder_names=new_folders,
                scanned_at=current.scanned_at,
                truncated=truncated,
                generation=current.generation
            )
        update_index(index.generation, merge)

    return index.folder_names | hits

def resolve_photo_folder_path(
    folder_name: str,
    settings: Settings
) -> Result:
    dir_path, _ = effective_photos_dir(settings)
    if not dir_path:
        return "err", "unconfigured"
    
    if not is_photo_folder_name(folder_name):
        return "err", "invalid_name"
        
    try:
        base = Path(dir_path).resolve()
        candidate = (Path(dir_path) / folder_name).resolve()
    except OSError:
        return "err", "unavailable"
        
    try:
        candidate.relative_to(base)
    except ValueError:
        return "err", "unavailable"
        
    return "ok", candidate

_open_gate_lock = threading.Lock()
_last_open_monotonic: float | None = None

def open_photo_folder(
    folder_name: str,
    settings: Settings,
    clock: Callable[[], float]
) -> Result:
    global _last_open_monotonic
    
    path_res = resolve_photo_folder_path(folder_name, settings)
    if path_res[0] == "err":
        return path_res
        
    path = path_res[1]
    
    with _open_gate_lock:
        now = clock()
        if _last_open_monotonic is not None:
            elapsed = now - _last_open_monotonic
            remaining = settings.shipping_photos_open_min_interval_seconds - elapsed
            if remaining > 0:
                return "err", RateLimited(remaining_seconds=remaining)
        
        _last_open_monotonic = now
        
    if not path.is_dir():
        current_gen = _cached_index.generation if _cached_index else 0
        def evict(current: PhotoFolderIndex) -> PhotoFolderIndex:
            new_folders = set(current.folder_names)
            new_folders.discard(folder_name)
            return PhotoFolderIndex(
                status=current.status,
                folder_names=new_folders,
                scanned_at=current.scanned_at,
                truncated=current.truncated,
                generation=current.generation
            )
        update_index(current_gen, evict)
        return "err", "not_found"
        
    try:
        os.startfile(path)
        logger.info("Opened photo folder: %s", path)
        return "ok", folder_name
    except Exception as e:
        logger.error("Failed to open %s: %s", path, e)
        return "err", "shell_error"
