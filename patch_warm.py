import re

with open("backend/app/services/photo_warm.py", "r") as f:
    content = f.read()

content = content.replace(
    """from backend.app.services.photo_files import resolve_file_index, PhotoFileListStatus""",
    """from backend.app.services.photo_files import resolve_file_index, PhotoFileListStatus, FolderKey, ROOT"""
)

content = content.replace(
    """_warm_queue: Deque[str] = deque()
_warm_known: Set[str] = set()""",
    """_warm_queue: Deque[FolderKey] = deque()
_warm_known: Set[FolderKey] = set()"""
)

content = content.replace(
    """def enqueue_warm(date_folder: str, settings: Settings) -> None:""",
    """def enqueue_warm(date_folder: str, sub_folder: str, settings: Settings) -> None:"""
)

content = content.replace(
    """        if date_folder in _warm_known:
            return
            
        if len(_warm_queue) >= settings.shipping_photos_thumb_warm_queue_max_folders:
            oldest = _warm_queue.popleft()
            _warm_known.discard(oldest)
            
        _warm_queue.append(date_folder)
        _warm_known.add(date_folder)""",
    """        key = (date_folder, sub_folder)
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
        _warm_known.add(key)"""
)

content = content.replace(
    """        date_folder = _next_folder_or_retire(stop)
        if date_folder is None:
            return

        try:
            _process_folder(date_folder, settings, stop)
        except Exception as e:
            logger.debug("Warm worker caught exception for folder %s: %s", date_folder, e)
        finally:
            with _warm_lock:
                _warm_known.discard(date_folder)""",
    """        key = _next_folder_or_retire(stop)
        if key is None:
            return

        try:
            _process_folder(key, settings, stop)
        except Exception as e:
            logger.debug("Warm worker caught exception for folder %s: %s", key, e)
        finally:
            with _warm_lock:
                _warm_known.discard(key)"""
)

content = content.replace(
    """def _next_folder_or_retire(stop: threading.Event) -> str | None:""",
    """def _next_folder_or_retire(stop: threading.Event) -> Union[FolderKey, None]:"""
)

# wait, I need to add typing.Union for Union[FolderKey, None] since | None is Python 3.10 and might be fine but maybe Union is safer. Let's see if typing has Union. Yes, `from typing import Set, Deque, Union` is better.
content = content.replace(
    """from typing import Set, Deque""",
    """from typing import Set, Deque, Union"""
)

content = content.replace(
    """def _process_folder(date_folder: str, settings: Settings, stop: threading.Event) -> None:
    clock = time.monotonic
    index = resolve_file_index(date_folder, settings, clock)
    
    if index.status != PhotoFileListStatus.OK:
        logger.debug("Warm worker skipping folder %s due to non-OK index status: %s", date_folder, index.status)
        return""",
    """def _process_folder(key: FolderKey, settings: Settings, stop: threading.Event) -> None:
    clock = time.monotonic
    date_folder, sub_folder = key
    index = resolve_file_index(date_folder, sub_folder, settings, clock)
    
    if index.status != PhotoFileListStatus.OK:
        logger.debug("Warm worker skipping folder %s due to non-OK index status: %s", key, index.status)
        return"""
)

content = content.replace(
    """                status, outcome = generate_once(date_folder, entry.name, index, "warm", settings)""",
    """                status, outcome = generate_once(date_folder, sub_folder, entry.name, index, "warm", settings)"""
)

with open("backend/app/services/photo_warm.py", "w") as f:
    f.write(content)
