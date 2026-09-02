import json
import os
from datetime import datetime

import pytest
from backend.app.config import Settings
import backend.app.services.runtime_config as rc
from backend.app.services.runtime_config import (
    RuntimeConfigWriteError,
    effective_photos_dir,
    load_runtime_config,
    save_photos_dir,
)

@pytest.fixture
def mock_runtime_root(tmp_path, monkeypatch):
    monkeypatch.setattr(rc, "_runtime_root", lambda: tmp_path)
    return tmp_path

@pytest.fixture
def reset_cache():
    rc._cached_config = None
    yield
    rc._cached_config = None

def test_missing_file_empty_config(mock_runtime_root, reset_cache):
    config = load_runtime_config()
    assert config == {"shipping_photos_dir": None, "updated_at": None}
    assert not (mock_runtime_root / "runtime-config.json").exists()

def test_round_trip(mock_runtime_root, reset_cache):
    class Clock:
        def isoformat(self):
            return "2026-09-01T00:00:00"

    save_photos_dir("C:\\photos", clock=Clock)
    
    # Read from cache
    config = load_runtime_config()
    assert config["shipping_photos_dir"] == "C:\\photos"
    
    # Clear cache, read from disk
    rc._cached_config = None
    config2 = load_runtime_config()
    assert config2["shipping_photos_dir"] == "C:\\photos"
    assert config2["updated_at"] == "2026-09-01T00:00:00"

def test_corrupt_store_preserves_file(mock_runtime_root, reset_cache):
    path = mock_runtime_root / "runtime-config.json"
    
    # Not valid JSON
    path.write_text("{bad", encoding="utf-8")
    config = load_runtime_config()
    assert config == {"shipping_photos_dir": None, "updated_at": None}
    assert path.read_text(encoding="utf-8") == "{bad"
    
    # JSON of wrong type (list)
    rc._cached_config = None
    path.write_text("[]", encoding="utf-8")
    assert load_runtime_config() == {"shipping_photos_dir": None, "updated_at": None}
    assert path.read_text(encoding="utf-8") == "[]"
    
    # JSON of wrong type (bare string)
    rc._cached_config = None
    path.write_text('"C:\\\\photos"', encoding="utf-8")
    assert load_runtime_config() == {"shipping_photos_dir": None, "updated_at": None}
    
    # JSON right type, wrong value type
    rc._cached_config = None
    path.write_text('{"shipping_photos_dir": 123}', encoding="utf-8")
    assert load_runtime_config() == {"shipping_photos_dir": None, "updated_at": None}

def test_atomic_write_preserves_original_on_failure(mock_runtime_root, reset_cache, monkeypatch):
    class Clock:
        def isoformat(self):
            return "2026-09-01T00:00:00"
            
    path = mock_runtime_root / "runtime-config.json"
    path.write_text('{"shipping_photos_dir": "old"}', encoding="utf-8")
    
    def mock_replace(src, dst):
        raise OSError("Simulated failure")
        
    monkeypatch.setattr(os, "replace", mock_replace)
    
    with pytest.raises(RuntimeConfigWriteError):
        save_photos_dir("new", clock=Clock)
        
    assert path.read_text(encoding="utf-8") == '{"shipping_photos_dir": "old"}'
    # The temp file should be cleaned up, or at least created in the same directory.
    temps = list(mock_runtime_root.glob("*.tmp"))
    assert len(temps) == 0

def test_effective_photos_dir_precedence(mock_runtime_root, reset_cache):
    # Store set / Env set -> Store wins
    save_photos_dir("C:\\store")
    settings = Settings(shipping_photos_dir="C:\\env")
    assert effective_photos_dir(settings) == ("C:\\store", "runtime")
    
    # Store set / Env not set -> Store wins
    settings = Settings(shipping_photos_dir="")
    assert effective_photos_dir(settings) == ("C:\\store", "runtime")
    
    # Store not set / Env set -> Env wins
    rc._cached_config = {"shipping_photos_dir": None, "updated_at": None}
    settings = Settings(shipping_photos_dir="C:\\env")
    assert effective_photos_dir(settings) == ("C:\\env", "env")
    
    # Neither set -> Unset
    settings = Settings(shipping_photos_dir="")
    assert effective_photos_dir(settings) == ("", "unset")

def test_blank_and_absent_are_same_state(mock_runtime_root, reset_cache):
    # Missing file
    settings = Settings(shipping_photos_dir="C:\\env")
    assert effective_photos_dir(settings) == ("C:\\env", "env")
    
    # Missing key
    rc._cached_config = None
    (mock_runtime_root / "runtime-config.json").write_text('{}', encoding="utf-8")
    assert effective_photos_dir(settings) == ("C:\\env", "env")
    
    # Stored ""
    rc._cached_config = None
    (mock_runtime_root / "runtime-config.json").write_text('{"shipping_photos_dir": ""}', encoding="utf-8")
    assert effective_photos_dir(settings) == ("C:\\env", "env")
    
    # Stored whitespace
    rc._cached_config = None
    (mock_runtime_root / "runtime-config.json").write_text('{"shipping_photos_dir": "   "}', encoding="utf-8")
    assert effective_photos_dir(settings) == ("C:\\env", "env")

