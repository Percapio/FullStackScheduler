import pytest
import os
import time
from pathlib import Path
from PIL import Image

from backend.app.config import Settings
from backend.app.services.photo_files import PhotoFileIndex, PhotoFileListStatus, PhotoFileEntry
from backend.app.services.photo_thumbnails import generate_once, _get_cache_dir

@pytest.fixture
def mock_cache_dir(monkeypatch, tmp_path):
    cache_path = tmp_path / "cache"
    monkeypatch.setattr("backend.app.services.photo_thumbnails._get_cache_dir", lambda: cache_path)
    cache_path.mkdir()
    return cache_path

@pytest.fixture(autouse=True)
def mock_runtime_config(monkeypatch):
    import backend.app.services.runtime_config as rc
    rc._cached_config = None
    monkeypatch.setattr(rc, "load_runtime_config", lambda: {})

def test_resolve_thumbnail_success(tmp_path, mock_cache_dir):
    settings = Settings(
        shipping_photos_dir=str(tmp_path),
        shipping_photos_thumb_max_edge_px=100
    )
    (tmp_path / "2023_01_01").mkdir()
    
    # Create valid image
    img_path = tmp_path / "2023_01_01" / "valid.jpg"
    img = Image.new("RGB", (2000, 2000), color="red")
    img.save(img_path, format="JPEG")
    
    idx = PhotoFileIndex(
        status=PhotoFileListStatus.OK,
        entries=[],
        by_name={"valid.jpg": PhotoFileEntry("valid.jpg", 100, 100, "100-100", True)},
        total_bytes=100,
        scanned_at=0,
        truncated=False
    )
    
    res, result = generate_once("2023_01_01", "valid.jpg", idx, "interactive", settings)
    assert res == "ok"
    assert result.media_type == "image/webp"
    assert result.path.exists()
    
    # Check dimensions
    with Image.open(result.path) as t:
        assert t.size == (100, 100)

def test_resolve_thumbnail_exif_transpose(tmp_path, mock_cache_dir):
    settings = Settings(
        shipping_photos_dir=str(tmp_path),
        shipping_photos_thumb_max_edge_px=100
    )
    (tmp_path / "2023_01_01").mkdir()
    
    img_path = tmp_path / "2023_01_01" / "exif.jpg"
    img = Image.new("RGB", (200, 100), color="red")
    # EXIF orientation 6 (Rotate 90 CW)
    exif_data = img.getexif()
    exif_data[274] = 6 
    img.save(img_path, format="JPEG", exif=exif_data)
    
    idx = PhotoFileIndex(
        status=PhotoFileListStatus.OK,
        entries=[],
        by_name={"exif.jpg": PhotoFileEntry("exif.jpg", 100, 100, "100-100", True)},
        total_bytes=100,
        scanned_at=0,
        truncated=False
    )
    
    res, result = generate_once("2023_01_01", "exif.jpg", idx, "interactive", settings)
    assert res == "ok"
    
    with Image.open(result.path) as t:
        # Before transpose, it was 200x100.
        # Orientation 6 means the camera was rotated 90 deg.
        # Transpose will swap dimensions to 100x200.
        # Then thumbnail will fit to max_edge=100. So it becomes 50x100.
        assert t.size == (50, 100)

def test_resolve_thumbnail_cache_unavailable(tmp_path, mock_cache_dir, monkeypatch):
    settings = Settings(
        shipping_photos_dir=str(tmp_path),
        shipping_photos_thumb_max_edge_px=100
    )
    (tmp_path / "2023_01_01").mkdir()
    
    img_path = tmp_path / "2023_01_01" / "valid.jpg"
    img = Image.new("RGB", (2000, 2000), color="red")
    img.save(img_path, format="JPEG")
    
    idx = PhotoFileIndex(
        status=PhotoFileListStatus.OK,
        entries=[],
        by_name={"valid.jpg": PhotoFileEntry("valid.jpg", 100, 100, "100-100", True)},
        total_bytes=100,
        scanned_at=0,
        truncated=False
    )
    
    # Mock replace to raise OSError
    import pathlib
    original_replace = pathlib.Path.replace
    def mock_replace(self, target):
        if self.suffix.startswith(".tmp"):
            raise OSError("Mock disk full")
        return original_replace(self, target)
        
    monkeypatch.setattr(pathlib.Path, "replace", mock_replace)
    
    res, result = generate_once("2023_01_01", "valid.jpg", idx, "interactive", settings)
    assert res == "err"
    assert result == "cache_unavailable"
    
    # Ensure no sentinel was written
    cache_key = "2023_01_01_valid.jpg_100-100.webp"
    assert not (mock_cache_dir / cache_key).exists()

def test_resolve_thumbnail_undecodable(tmp_path, mock_cache_dir):
    settings = Settings(shipping_photos_dir=str(tmp_path))
    (tmp_path / "2023_01_01").mkdir()
    
    # Create bad image
    img_path = tmp_path / "2023_01_01" / "bad.jpg"
    img_path.write_bytes(b"not an image")
    
    idx = PhotoFileIndex(
        status=PhotoFileListStatus.OK,
        entries=[],
        by_name={"bad.jpg": PhotoFileEntry("bad.jpg", 12, 100, "100-12", True)},
        total_bytes=12,
        scanned_at=0,
        truncated=False
    )
    
    res, msg = generate_once("2023_01_01", "bad.jpg", idx, "interactive", settings)
    assert res == "err"
    assert msg == "not_previewable"
    
    # Sentinel should exist
    sentinel = mock_cache_dir / "2023_01_01_bad.jpg_100-12.webp"
    assert sentinel.exists()
    assert sentinel.stat().st_size == 0

def test_resolve_thumbnail_sweep(tmp_path, mock_cache_dir):
    settings = Settings(
        shipping_photos_dir=str(tmp_path),
        shipping_photos_thumb_sweep_every_n_writes=2,
        shipping_photos_thumb_cache_max_bytes=10
    )
    (tmp_path / "2023_01_01").mkdir()
    
    # We will just write some fake cache entries directly to test sweeping
    p1 = mock_cache_dir / "old.webp"
    p1.write_bytes(b"1234567890123") # 13 bytes
    
    # Change mtime so p1 is older
    os.utime(p1, (0, 0))
    
    # Now generate one thumbnail, which will trigger sweep since it's the 2nd write? No we need 2 writes
    import backend.app.services.photo_thumbnails as pt
    pt._write_counter = 1
    
    img_path = tmp_path / "2023_01_01" / "valid.jpg"
    img = Image.new("RGB", (10, 10), color="red")
    img.save(img_path, format="JPEG")
    
    idx = PhotoFileIndex(
        status=PhotoFileListStatus.OK,
        entries=[],
        by_name={"valid.jpg": PhotoFileEntry("valid.jpg", 100, 100, "100-100", True)},
        total_bytes=100,
        scanned_at=0,
        truncated=False
    )
    
    res, result = generate_once("2023_01_01", "valid.jpg", idx, "interactive", settings)
    assert res == "ok"
    
    # Sweep should have run, old should be deleted
    assert not p1.exists()

from backend.app.services.photo_thumbnails import (
    acquire_thumbnail_permit
)

@pytest.fixture(autouse=True)
def reset_gate():
    import backend.app.services.photo_thumbnails as pt
    pt._gate_active = 0
    pt._gate_waiting = 0

def test_gate_interactive_blocks_and_grants(tmp_path):
    settings = Settings(
        shipping_photos_thumb_max_concurrent=1,
        shipping_photos_thumb_max_waiters=1,
        shipping_photos_thumb_queue_wait_seconds=5.0
    )
    
    # Take the only permit
    permit1 = acquire_thumbnail_permit("interactive", settings)
    ctx1 = permit1.__enter__()
    assert ctx1[0] == "ok"
    
    # Thread 2 should block
    import threading
    result = []
    def thread2():
        with acquire_thumbnail_permit("interactive", settings) as (status, _):
            result.append(status)
            
    t = threading.Thread(target=thread2)
    t.start()
    
    # Ensure it's waiting
    time.sleep(0.1)
    import backend.app.services.photo_thumbnails as pt
    assert pt._gate_waiting == 1
    assert not result
    
    # Release permit1
    permit1.__exit__(None, None, None)
    
    t.join()
    assert result == ["ok"]
    assert pt._gate_waiting == 0
    assert pt._gate_active == 0

def test_gate_interactive_rejects_when_waiters_full():
    settings = Settings(
        shipping_photos_thumb_max_concurrent=0,
        shipping_photos_thumb_max_waiters=0,
        shipping_photos_thumb_queue_wait_seconds=5.0
    )
    with acquire_thumbnail_permit("interactive", settings) as (status, reason):
        assert status == "err"
        assert reason == "saturated"
        
    import backend.app.services.photo_thumbnails as pt
    assert pt._gate_waiting == 0
    assert pt._gate_active == 0

def test_gate_warm_rejects_immediately_no_waiter():
    settings = Settings(
        shipping_photos_thumb_max_concurrent=0,
        shipping_photos_thumb_max_waiters=5,
        shipping_photos_thumb_queue_wait_seconds=5.0
    )
    with acquire_thumbnail_permit("warm", settings) as (status, reason):
        assert status == "err"
        assert reason == "saturated"
        
    import backend.app.services.photo_thumbnails as pt
    assert pt._gate_waiting == 0
    assert pt._gate_active == 0

def test_gate_waiter_count_returns_to_zero_after_timeout():
    settings = Settings(
        shipping_photos_thumb_max_concurrent=0,
        shipping_photos_thumb_max_waiters=5,
        shipping_photos_thumb_queue_wait_seconds=0.1
    )
    
    with acquire_thumbnail_permit("interactive", settings) as (status, reason):
        assert status == "err"
        assert reason == "timeout"
        
    import backend.app.services.photo_thumbnails as pt
    assert pt._gate_waiting == 0

def test_gate_waiter_count_returns_to_zero_after_exception():
    settings = Settings(
        shipping_photos_thumb_max_concurrent=1,
        shipping_photos_thumb_max_waiters=5,
        shipping_photos_thumb_queue_wait_seconds=5.0
    )
    
    try:
        with acquire_thumbnail_permit("interactive", settings):
            raise ValueError("test")
    except ValueError:
        pass
        
    import backend.app.services.photo_thumbnails as pt
    assert pt._gate_active == 0

def test_pillow_calls_draft(tmp_path, monkeypatch, mock_cache_dir):
    settings = Settings(
        shipping_photos_dir=str(tmp_path),
        shipping_photos_thumb_max_edge_px=100
    )
    (tmp_path / "2023_01_01").mkdir()
    
    img_path = tmp_path / "2023_01_01" / "draft.jpg"
    img = Image.new("RGB", (2000, 2000), color="red")
    img.save(img_path, format="JPEG")
    
    idx = PhotoFileIndex(
        status=PhotoFileListStatus.OK,
        entries=[],
        by_name={"draft.jpg": PhotoFileEntry("draft.jpg", 100, 100, "100-100", True)},
        total_bytes=100,
        scanned_at=0,
        truncated=False
    )
    
    draft_calls = []
    import PIL.JpegImagePlugin
    original_draft = PIL.JpegImagePlugin.JpegImageFile.draft
    def mock_draft(self, mode, size):
        draft_calls.append(size)
        return original_draft(self, mode, size)
        
    monkeypatch.setattr(PIL.JpegImagePlugin.JpegImageFile, "draft", mock_draft)
    
    res, result = generate_once("2023_01_01", "draft.jpg", idx, "interactive", settings)
    assert res == "ok"
    assert len(draft_calls) > 0
import threading
import time
from backend.app.services.photo_thumbnails import generate_once, _inflight, _inflight_lock

def test_single_flight_success(tmp_path, mock_cache_dir, monkeypatch):
    settings = Settings(
        shipping_photos_dir=str(tmp_path),
        shipping_photos_thumb_max_edge_px=100,
        shipping_photos_thumb_queue_wait_seconds=5.0
    )
    (tmp_path / "2023_01_01").mkdir()
    
    img_path = tmp_path / "2023_01_01" / "sf.jpg"
    img = Image.new("RGB", (200, 200), color="blue")
    img.save(img_path, format="JPEG")
    
    idx = PhotoFileIndex(
        status=PhotoFileListStatus.OK,
        entries=[],
        by_name={"sf.jpg": PhotoFileEntry("sf.jpg", 100, 100, "100-100", True)},
        total_bytes=100,
        scanned_at=0,
        truncated=False
    )

    import backend.app.services.photo_thumbnails as pt
    
    # We want to intercept _generate_impl to block and verify N=1 calls
    calls = []
    real_gen = pt._generate_impl
    ev_start = threading.Event()
    ev_proceed = threading.Event()
    
    def mock_gen(*args, **kwargs):
        calls.append(1)
        ev_start.set()
        ev_proceed.wait()
        return real_gen(*args, **kwargs)
        
    monkeypatch.setattr(pt, "_generate_impl", mock_gen)
    
    res1 = []
    res2 = []
    
    def worker1():
        res1.append(generate_once("2023_01_01", "sf.jpg", idx, "interactive", settings))
    def worker2():
        res2.append(generate_once("2023_01_01", "sf.jpg", idx, "interactive", settings))
        
    t1 = threading.Thread(target=worker1)
    t1.start()
    
    ev_start.wait()
    
    # Now t1 is in _generate_impl (holding permit, registered in inflight map)
    t2 = threading.Thread(target=worker2)
    t2.start()
    
    # Ensure t2 is waiting
    time.sleep(0.1)
    # Check that it's waiting WITHOUT holding a permit.
    # We started t1 first, it took the only permit? Actually max_concurrent is 8.
    # But wait, how do we know it didn't take a permit?
    # Because it is blocked on record.done.wait() and we know it didn't call _generate_impl again!
    assert len(calls) == 1
    
    ev_proceed.set()
    t1.join()
    t2.join()
    
    assert len(calls) == 1
    assert res1[0][0] == "ok"
    assert res2[0][0] == "ok"
    
    with _inflight_lock:
        assert len(_inflight) == 0

def test_single_flight_failure(tmp_path, mock_cache_dir, monkeypatch):
    settings = Settings(
        shipping_photos_dir=str(tmp_path),
        shipping_photos_thumb_max_edge_px=100,
        shipping_photos_thumb_queue_wait_seconds=5.0
    )
    (tmp_path / "2023_01_01").mkdir()
    
    img_path = tmp_path / "2023_01_01" / "fail.jpg"
    img_path.write_bytes(b"not an image")
    
    idx = PhotoFileIndex(
        status=PhotoFileListStatus.OK,
        entries=[],
        by_name={"fail.jpg": PhotoFileEntry("fail.jpg", 100, 100, "100-100", True)},
        total_bytes=100,
        scanned_at=0,
        truncated=False
    )
    
    import backend.app.services.photo_thumbnails as pt
    
    calls = []
    real_gen = pt._generate_impl
    ev_start = threading.Event()
    ev_proceed = threading.Event()
    
    def mock_gen(*args, **kwargs):
        calls.append(1)
        ev_start.set()
        ev_proceed.wait()
        return real_gen(*args, **kwargs)
        
    monkeypatch.setattr(pt, "_generate_impl", mock_gen)
    
    res1 = []
    res2 = []
    
    def worker1():
        res1.append(generate_once("2023_01_01", "fail.jpg", idx, "interactive", settings))
    def worker2():
        res2.append(generate_once("2023_01_01", "fail.jpg", idx, "interactive", settings))
        
    t1 = threading.Thread(target=worker1)
    t1.start()
    
    ev_start.wait()
    t2 = threading.Thread(target=worker2)
    t2.start()
    
    time.sleep(0.1)
    ev_proceed.set()
    t1.join()
    t2.join()
    
    assert len(calls) == 1
    assert res1[0][0] == "err"
    assert res1[0][1] == "not_previewable"
    assert res2[0][0] == "err"
    assert res2[0][1] == "not_previewable"
    
    with _inflight_lock:
        assert len(_inflight) == 0

def test_single_flight_timeout(tmp_path, mock_cache_dir, monkeypatch):
    settings = Settings(
        shipping_photos_dir=str(tmp_path),
        shipping_photos_thumb_max_edge_px=100,
        shipping_photos_thumb_queue_wait_seconds=0.1
    )
    (tmp_path / "2023_01_01").mkdir()
    
    img_path = tmp_path / "2023_01_01" / "to.jpg"
    img = Image.new("RGB", (200, 200), color="blue")
    img.save(img_path, format="JPEG")
    
    idx = PhotoFileIndex(
        status=PhotoFileListStatus.OK,
        entries=[],
        by_name={"to.jpg": PhotoFileEntry("to.jpg", 100, 100, "100-100", True)},
        total_bytes=100,
        scanned_at=0,
        truncated=False
    )
    
    import backend.app.services.photo_thumbnails as pt
    
    ev_start = threading.Event()

    real_gen = pt._generate_impl
    def mock_gen(*args, **kwargs):
        ev_start.set()
        time.sleep(0.5)
        return real_gen(*args, **kwargs)
        
    monkeypatch.setattr(pt, "_generate_impl", mock_gen)
    
    res1 = []
    res2 = []
    
    def worker1():
        try:
            res1.append(generate_once("2023_01_01", "to.jpg", idx, "interactive", settings))
        except Exception as e:
            print("W1 ERROR:", repr(e))
    def worker2():
        res2.append(generate_once("2023_01_01", "to.jpg", idx, "interactive", settings))
        
    t1 = threading.Thread(target=worker1)
    t1.start()
    
    ev_start.wait()
    t2 = threading.Thread(target=worker2)
    t2.start()
    
    t2.join()
    assert res2[0][0] == "err"
    assert res2[0][1] == "timeout"
    
    t1.join()
    assert res1[0][0] == "ok"
    
    with _inflight_lock:
        assert len(_inflight) == 0

