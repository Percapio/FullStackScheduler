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
    shipping_photos_auto_copy_enabled: bool | None
    shipping_photos_auto_copy_source: str | None
    shipping_photos_auto_copy_time: str | None

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
        
        default_config: RuntimeConfig = {
            "shipping_photos_dir": None,
            "updated_at": None,
            "shipping_photos_auto_copy_enabled": None,
            "shipping_photos_auto_copy_source": None,
            "shipping_photos_auto_copy_time": None,
        }
        
        if not path.exists():
            _cached_config = default_config
            return _cached_config

        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
                
            if not isinstance(data, dict):
                raise ValueError("JSON is not an object")
                
            dir_val = data.get("shipping_photos_dir")
            if dir_val is not None and not isinstance(dir_val, str):
                logger.warning("shipping_photos_dir must be a string or null, resetting")
                dir_val = None
                
            auto_copy_enabled = data.get("shipping_photos_auto_copy_enabled")
            if auto_copy_enabled is not None and not isinstance(auto_copy_enabled, bool):
                logger.warning("shipping_photos_auto_copy_enabled must be a bool or null, resetting")
                auto_copy_enabled = None
                
            auto_copy_source = data.get("shipping_photos_auto_copy_source")
            if auto_copy_source is not None and not isinstance(auto_copy_source, str):
                logger.warning("shipping_photos_auto_copy_source must be a string or null, resetting")
                auto_copy_source = None
                
            auto_copy_time = data.get("shipping_photos_auto_copy_time")
            if auto_copy_time is not None:
                if not isinstance(auto_copy_time, str):
                    logger.warning("shipping_photos_auto_copy_time must be a string or null, resetting")
                    auto_copy_time = None
                else:
                    import re
                    if not re.match(r"^([01]\d|2[0-3]):[0-5]\d$", auto_copy_time):
                        logger.warning("shipping_photos_auto_copy_time must match HH:MM, resetting")
                        auto_copy_time = None
                
            _cached_config = {
                "shipping_photos_dir": dir_val,
                "updated_at": data.get("updated_at"),
                "shipping_photos_auto_copy_enabled": auto_copy_enabled,
                "shipping_photos_auto_copy_source": auto_copy_source,
                "shipping_photos_auto_copy_time": auto_copy_time,
            }
        except Exception as e:
            logger.warning("Failed to load runtime-config.json, returning empty config: %s", e)
            _cached_config = default_config
            
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

from typing import Mapping, Any, Callable

def save_runtime_config(changes: Mapping[str, Any], clock: Callable[[], datetime]) -> None:
    global _cached_config
    
    root = _runtime_root()
    target_path = root / "runtime-config.json"
    temp_path = root / f"runtime-config-{os.getpid()}-{threading.get_ident()}.json.tmp"
    
    with _config_lock:
        if _cached_config is None:
            # Force load if not loaded, inside lock
            _cached_config_backup = _cached_config
            _config_lock.release()
            try:
                load_runtime_config()
            finally:
                _config_lock.acquire()

        new_config: RuntimeConfig = dict(_cached_config) if _cached_config else {
            "shipping_photos_dir": None,
            "updated_at": None,
            "shipping_photos_auto_copy_enabled": None,
            "shipping_photos_auto_copy_source": None,
            "shipping_photos_auto_copy_time": None,
        }
        
        for k, v in changes.items():
            new_config[k] = v
            
        if "updated_at" not in changes:
            new_config["updated_at"] = clock().isoformat()
            
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
            
        _cached_config = new_config

def save_photos_dir(path: str, clock=datetime.now) -> None:
    save_runtime_config({"shipping_photos_dir": path}, clock)
