import json
import logging
import os
import threading
from datetime import datetime
from typing import TypedDict, Tuple

from ..config import Settings, _runtime_root

logger = logging.getLogger(__name__)

class RuntimeConfig(TypedDict):
    shipping_photos_dir: str | None
    updated_at: str | None

_config_lock = threading.Lock()
_cached_config: RuntimeConfig | None = None

class RuntimeConfigWriteError(Exception):
    pass

def load_runtime_config() -> RuntimeConfig:
    global _cached_config
    with _config_lock:
        if _cached_config is not None:
            return _cached_config

        path = _runtime_root() / "runtime-config.json"
        if not path.exists():
            _cached_config = {"shipping_photos_dir": None, "updated_at": None}
            return _cached_config

        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
                
            if not isinstance(data, dict):
                raise ValueError("JSON is not an object")
                
            dir_val = data.get("shipping_photos_dir")
            if dir_val is not None and not isinstance(dir_val, str):
                raise ValueError("shipping_photos_dir must be a string or null")
                
            _cached_config = {
                "shipping_photos_dir": dir_val,
                "updated_at": data.get("updated_at")
            }
        except Exception as e:
            logger.warning("Failed to load runtime-config.json, returning empty config: %s", e)
            _cached_config = {"shipping_photos_dir": None, "updated_at": None}
            
        return _cached_config

def effective_photos_dir(settings: Settings) -> Tuple[str, str]:
    config = load_runtime_config()
    runtime_dir = config.get("shipping_photos_dir")
    
    if runtime_dir is not None and runtime_dir.strip():
        return runtime_dir, "runtime"
        
    env_dir = settings.shipping_photos_dir
    if env_dir and env_dir.strip():
        return env_dir, "env"
        
    return "", "unset"

def save_photos_dir(path: str, clock=datetime.now) -> None:
    global _cached_config
    
    root = _runtime_root()
    target_path = root / "runtime-config.json"
    temp_path = root / f"runtime-config-{os.getpid()}-{threading.get_ident()}.json.tmp"
    
    updated_at = clock().isoformat()
    new_config: RuntimeConfig = {
        "shipping_photos_dir": path,
        "updated_at": updated_at
    }
    
    try:
        with open(temp_path, "w", encoding="utf-8") as f:
            json.dump(new_config, f, indent=2)
            f.flush()
            os.fsync(f.fileno())
            
        os.replace(temp_path, target_path)
    except Exception as e:
        if temp_path.exists():
            try:
                temp_path.unlink()
            except Exception:
                pass
        raise RuntimeConfigWriteError(f"Failed to save runtime config: {e}")
        
    with _config_lock:
        _cached_config = new_config
