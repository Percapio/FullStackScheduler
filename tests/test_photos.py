import os
import threading
from pathlib import Path

import pytest
from pydantic_settings import SettingsConfigDict

from backend.app.config import Settings
from backend.app.services.shipping_photos import (
    PHOTO_FOLDER_PATTERN,
    PhotoDirectoryStatus,
    PhotoFolderIndex,
    RateLimited,
    is_photo_folder_name,
    open_photo_folder,
    probe_missing_folders,
    resolve_folder_index,
    resolve_photo_folder_path,
    update_index,
)
import backend.app.services.shipping_photos as sp

@pytest.fixture
def mock_clock():
    class Clock:
        def __init__(self):
            self.time = 0.0
        def __call__(self):
            return self.time
        def advance(self, amount):
            self.time += amount
    return Clock()

@pytest.fixture
def reset_module_state():
    # Reset globals before each test
    sp._cached_index = None
    sp._last_open_monotonic = None
    yield
    sp._cached_index = None
    sp._last_open_monotonic = None

def test_grammar():
    # Accepted
    assert is_photo_folder_name("2023_07_24")
    assert is_photo_folder_name("0000_00_00")
    assert is_photo_folder_name("2023_13_45")
    
    # Rejected
    assert not is_photo_folder_name("..")
    assert not is_photo_folder_name(".")
    assert not is_photo_folder_name("2023-07-24")
    assert not is_photo_folder_name("2023_07_24/..")
    assert not is_photo_folder_name("..\\2023_07_24")
    assert not is_photo_folder_name("C:\\Windows")
    assert not is_photo_folder_name("\\\\host\\share")
    assert not is_photo_folder_name("2023_07_2")
    assert not is_photo_folder_name("2023_07_244")
    assert not is_photo_folder_name("")
    assert not is_photo_folder_name(" 2023_07_24")
    assert not is_photo_folder_name("٢٠٢٣_٠٧_٢٤")

def test_resolve_photo_folder_path_blank_config():
    settings = Settings(shipping_photos_dir="")
    res = resolve_photo_folder_path("2023_07_24", settings)
    assert res == ("err", "unconfigured")

def test_resolve_photo_folder_path_containment(tmp_path):
    settings = Settings(shipping_photos_dir=str(tmp_path))
    (tmp_path / "2023_07_24").mkdir()
    
    res = resolve_photo_folder_path("2023_07_24", settings)
    assert res[0] == "ok"
    assert res[1] == (tmp_path / "2023_07_24").resolve()
    
    # Try directory traversal (caught by grammar, but verify)
    res2 = resolve_photo_folder_path("..", settings)
    assert res2 == ("err", "invalid_name")
    
    # If a junction points outside, it fails containment check.
    # Note: creating junctions in CI is often restricted, so this logic is checked by `candidate.relative_to(base)`.

def test_resolve_folder_index_caching(tmp_path, mock_clock, reset_module_state):
    settings = Settings(
        shipping_photos_dir=str(tmp_path),
        shipping_photos_index_ttl_seconds=10.0,
        shipping_photos_unavailable_ttl_seconds=2.0
    )
    
    (tmp_path / "2023_07_24").mkdir()
    (tmp_path / "invalid_name").mkdir()
    (tmp_path / "2023_07_25").touch() # file, not dir
    
    idx1 = resolve_folder_index(settings, mock_clock)
    assert idx1.status == PhotoDirectoryStatus.OK
    assert idx1.folder_names == {"2023_07_24"}
    
    # Within TTL, no filesystem call, generation unchanged
    mock_clock.advance(5.0)
    idx2 = resolve_folder_index(settings, mock_clock)
    assert idx2 is idx1
    
    # Expire OK cache
    mock_clock.advance(6.0)
    (tmp_path / "2023_07_26").mkdir()
    idx3 = resolve_folder_index(settings, mock_clock)
    assert idx3 is not idx1
    assert idx3.generation > idx1.generation
    assert idx3.folder_names == {"2023_07_24", "2023_07_26"}

def test_resolve_folder_index_unavailable(tmp_path, mock_clock, reset_module_state):
    settings = Settings(
        shipping_photos_dir=str(tmp_path / "nonexistent"),
        shipping_photos_unavailable_ttl_seconds=2.0
    )
    idx1 = resolve_folder_index(settings, mock_clock)
    assert idx1.status == PhotoDirectoryStatus.UNAVAILABLE
    
    mock_clock.advance(1.0)
    idx2 = resolve_folder_index(settings, mock_clock)
    assert idx2 is idx1
    
    # Expire unavailable cache
    mock_clock.advance(2.0)
    idx3 = resolve_folder_index(settings, mock_clock)
    assert idx3 is not idx1
    assert idx3.status == PhotoDirectoryStatus.UNAVAILABLE

def test_resolve_folder_index_unconfigured(mock_clock, reset_module_state):
    settings = Settings(shipping_photos_dir="")
    idx = resolve_folder_index(settings, mock_clock)
    assert idx.status == PhotoDirectoryStatus.UNCONFIGURED
    assert idx.folder_names == set()

def test_update_index_cas(tmp_path, mock_clock, reset_module_state):
    settings = Settings(shipping_photos_dir=str(tmp_path))
    idx = resolve_folder_index(settings, mock_clock)
    gen = idx.generation
    
    def mutate(current):
        return PhotoFolderIndex(
            status=current.status,
            folder_names={"new"},
            scanned_at=current.scanned_at,
            truncated=current.truncated,
            generation=current.generation
        )
    
    # Success CAS
    assert update_index(gen, mutate) is True
    assert sp._cached_index.folder_names == {"new"}
    
    # Failed CAS (wrong gen)
    assert update_index(gen - 1, mutate) is False

def test_probe_missing_folders(tmp_path, mock_clock, reset_module_state):
    settings = Settings(shipping_photos_dir=str(tmp_path))
    idx = resolve_folder_index(settings, mock_clock)
    assert idx.folder_names == set()
    
    # Create folder outside scan
    (tmp_path / "2023_07_24").mkdir()
    
    # Probe
    hits = probe_missing_folders({"2023_07_24", "2023_07_25", "invalid"}, idx, settings)
    assert hits == {"2023_07_24"}
    
    # Check cache merged
    assert sp._cached_index.folder_names == {"2023_07_24"}
    # Scanned at is NOT advanced
    assert sp._cached_index.scanned_at == idx.scanned_at

def test_open_photo_folder_gate(tmp_path, mock_clock, reset_module_state, monkeypatch):
    settings = Settings(
        shipping_photos_dir=str(tmp_path),
        shipping_photos_open_min_interval_seconds=2.0
    )
    (tmp_path / "2023_07_24").mkdir()
    
    calls = []
    def mock_startfile(path):
        calls.append(path)
        
    monkeypatch.setattr(os, "startfile", mock_startfile, raising=False)
    
    res1 = open_photo_folder("2023_07_24", settings, mock_clock)
    assert res1[0] == "ok"
    assert len(calls) == 1
    
    # Inside interval
    mock_clock.advance(1.0)
    res2 = open_photo_folder("2023_07_24", settings, mock_clock)
    assert res2[0] == "err"
    assert isinstance(res2[1], RateLimited)
    assert res2[1].remaining_seconds == 1.0
    assert len(calls) == 1
    
    # Outside interval
    mock_clock.advance(1.1)
    res3 = open_photo_folder("2023_07_24", settings, mock_clock)
    assert res3[0] == "ok"
    assert len(calls) == 2

def test_open_photo_folder_not_found_evicts(tmp_path, mock_clock, reset_module_state, monkeypatch):
    settings = Settings(shipping_photos_dir=str(tmp_path))
    (tmp_path / "2023_07_24").mkdir()
    
    idx = resolve_folder_index(settings, mock_clock)
    assert "2023_07_24" in idx.folder_names
    
    # Delete folder
    (tmp_path / "2023_07_24").rmdir()
    
    res = open_photo_folder("2023_07_24", settings, mock_clock)
    assert res == ("err", "not_found")
    
    # Assert evicted
    assert "2023_07_24" not in sp._cached_index.folder_names

def test_generation_monotonicity_across_invalidation(tmp_path, mock_clock, reset_module_state):
    settings = Settings(shipping_photos_dir=str(tmp_path / "dir1"))
    (tmp_path / "dir1").mkdir()
    (tmp_path / "dir1" / "2023_07_24").mkdir()
    
    # 1. Read index
    idx = resolve_folder_index(settings, mock_clock)
    gen = idx.generation
    assert "2023_07_24" in idx.folder_names
    
    # 2. Invalidate
    sp.invalidate_index()
    
    # 3. Rescan different directory
    settings = Settings(shipping_photos_dir=str(tmp_path / "dir2"))
    (tmp_path / "dir2").mkdir()
    (tmp_path / "dir2" / "2023_07_25").mkdir()
    
    idx2 = resolve_folder_index(settings, mock_clock)
    assert "2023_07_25" in idx2.folder_names
    assert "2023_07_24" not in idx2.folder_names
    assert idx2.generation > gen
    
    # 4. Attempt update_index with the captured generation (simulating delayed CAS)
    def mutate(current):
        folders = set(current.folder_names)
        folders.add("2023_07_24")
        return PhotoFolderIndex(
            status=current.status,
            folder_names=folders,
            scanned_at=current.scanned_at,
            truncated=current.truncated,
            generation=current.generation
        )
    
    cas_result = update_index(gen, mutate)
    assert cas_result is False
    
    # Ensure dir1's folder is not in dir2's index
    assert "2023_07_24" not in sp._cached_index.folder_names

def test_invalidate_index_idempotent():
    sp._cached_index = None
    sp.invalidate_index()
    assert sp._cached_index is None

def test_concurrent_scan(tmp_path, reset_module_state):
    import time
    settings = Settings(shipping_photos_dir=str(tmp_path))
    (tmp_path / "2023_07_24").mkdir()

    scan_entered = threading.Event()
    release = threading.Event()

    original_scandir = os.scandir
    iterator_count = 0

    def blocking_scandir(path):
        nonlocal iterator_count
        iterator_count += 1
        scan_entered.set()
        release.wait()
        return original_scandir(path)

    # We need to monkeypatch locally since we are in a thread
    os.scandir = blocking_scandir
    try:
        def worker1(res):
            res.append(resolve_folder_index(settings, time.monotonic))

        def worker2(res):
            res.append(resolve_folder_index(settings, time.monotonic))

        res1 = []
        res2 = []
        t1 = threading.Thread(target=worker1, args=(res1,))
        t2 = threading.Thread(target=worker2, args=(res2,))

        t1.start()
        scan_entered.wait() # A is inside the scan, holding the lock
        t2.start()
        time.sleep(0.1) # Give B time to block on the lock

        release.set() # Let both proceed
        t1.join()
        t2.join()

        assert iterator_count == 1
        assert res1[0] is res2[0] # Identity check proves double-checked freshness
    finally:
        os.scandir = original_scandir

def test_effective_photos_dir_priority(tmp_path, mock_clock, reset_module_state):
    import backend.app.services.runtime_config as rc
    from backend.app.services.runtime_config import save_photos_dir
    
    rc._cached_config = None
    
    (tmp_path / "env_dir").mkdir()
    (tmp_path / "env_dir" / "2023_07_24").mkdir()
    
    (tmp_path / "store_dir").mkdir()
    (tmp_path / "store_dir" / "2023_07_25").mkdir()
    
    settings = Settings(shipping_photos_dir=str(tmp_path / "env_dir"))
    
    # Write to store
    rc._cached_config = {"shipping_photos_dir": str(tmp_path / "store_dir"), "updated_at": "now"}
    
    idx = resolve_folder_index(settings, mock_clock)
    assert "2023_07_25" in idx.folder_names
    assert "2023_07_24" not in idx.folder_names
    
    rc._cached_config = None
