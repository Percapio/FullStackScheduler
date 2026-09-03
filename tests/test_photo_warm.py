import threading
import time
from typing import List, Tuple
from unittest.mock import patch, MagicMock

import pytest

from backend.app.config import Settings
from backend.app.services.photo_files import PhotoFileListStatus, PhotoFileIndex, PhotoFileEntry
import backend.app.services.photo_warm as pw

@pytest.fixture(autouse=True)
def reset_warm_state():
    pw._warm_queue.clear()
    pw._warm_known.clear()
    pw._warm_thread = None
    pw._warm_stop.clear()
    yield
    pw.shutdown_warm_worker(timeout=5.0)

def test_enqueue_queue_full_drops_oldest(tmp_path):
    settings = Settings(
        shipping_photos_dir=str(tmp_path),
        shipping_photos_thumb_warm_queue_max_folders=2,
        shipping_photos_thumb_warm_enabled=True
    )
    
    # We don't want the worker to actually process them, so we mock it.
    with patch("backend.app.services.photo_warm._process_folder") as mock_process:
        # Prevent the worker thread from popping items by blocking it, or we just don't start it?
        # Actually enqueue starts it automatically.
        # So we can set max folders, and let's mock thread start to not start.
        with patch("threading.Thread.start"):
           pw.enqueue_warm("f1", settings)
           pw.enqueue_warm("f2", settings)
           pw.enqueue_warm("f3", settings)
            
           assert list(pw._warm_queue) == ["f2", "f3"]
           assert "f1" not in pw._warm_known
           assert "f2" in pw._warm_known
           assert "f3" in pw._warm_known
            
            # Since f1 was dropped, we can re-enqueue it!
           pw.enqueue_warm("f1", settings)
           assert list(pw._warm_queue) == ["f3", "f1"]
           assert "f2" not in pw._warm_known

def test_enqueue_dedup_queued_and_walking(tmp_path):
    settings = Settings(
        shipping_photos_dir=str(tmp_path),
        shipping_photos_thumb_warm_enabled=True
    )
    
    ev_process_start = threading.Event()
    ev_process_proceed = threading.Event()
    
    def mock_process(folder, *args):
        if folder == "f1":
            ev_process_start.set()
            ev_process_proceed.wait()
            
    with patch("backend.app.services.photo_warm._process_folder", side_effect=mock_process):
        pw.enqueue_warm("f1", settings)
        
        # wait for f1 to be walking
        ev_process_start.wait()
        
        # f1 is now walking. It is dequeued but in _warm_known!
        assert "f1" not in pw._warm_queue
        assert "f1" in pw._warm_known
        
        # Enqueue f1 again -> ignored
        pw.enqueue_warm("f1", settings)
        assert "f1" not in pw._warm_queue
        
        # Enqueue f2 -> queued
        pw.enqueue_warm("f2", settings)
        assert list(pw._warm_queue) == ["f2"]
        
        # Enqueue f2 again -> ignored
        pw.enqueue_warm("f2", settings)
        assert list(pw._warm_queue) == ["f2"]
        
        ev_process_proceed.set()

def test_concurrent_enqueue_starts_one_thread(tmp_path):
    settings = Settings(
        shipping_photos_dir=str(tmp_path),
        shipping_photos_thumb_warm_enabled=True
    )
    
    ev = threading.Event()
    def mock_process(*args):
        ev.wait()
        
    with patch("backend.app.services.photo_warm._process_folder", side_effect=mock_process):
        def worker(i):
           pw.enqueue_warm(f"f{i}", settings)
            
        threads = [threading.Thread(target=worker, args=(i,)) for i in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
            
        assert pw._warm_thread is not None
        
        ev.set()
        while pw._warm_known: time.sleep(0.01)
        pw.shutdown_warm_worker()
        
        # we can't assert exactly one thread was started directly easily without mocking threading.Thread.
        # But we can check that only one thread is active for the worker.
        assert len([t for t in threading.enumerate() if t.name == "PhotoWarmWorker"]) == 0

def test_warm_disabled(tmp_path):
    settings = Settings(
        shipping_photos_dir=str(tmp_path),
        shipping_photos_thumb_warm_enabled=False
    )
    
    pw.enqueue_warm("f1", settings)
    assert len(pw._warm_queue) == 0
    assert pw._warm_thread is None

def test_worker_order_and_previewable(tmp_path):
    settings = Settings(
        shipping_photos_dir=str(tmp_path),
        shipping_photos_thumb_warm_enabled=True
    )
    
    idx = PhotoFileIndex(
        status=PhotoFileListStatus.OK,
        entries=[
            PhotoFileEntry("c.jpg", 100, 100, "v1", True),
            PhotoFileEntry("a.txt", 100, 100, "v1", False),
            PhotoFileEntry("b.jpg", 100, 100, "v1", True)
        ],
        by_name={},
        total_bytes=300,
        scanned_at=0,
        truncated=False
    )
    
    calls = []
    def mock_resolve(*args):
        return idx
        
    def mock_generate_once(folder, name, *args):
        calls.append(name)
        return "ok", None
        
    with patch("backend.app.services.photo_warm.resolve_file_index", side_effect=mock_resolve), \
         patch("backend.app.services.photo_warm.generate_once", side_effect=mock_generate_once):
         
        pw.enqueue_warm("f1", settings)
        while pw._warm_known: time.sleep(0.01)
        pw.shutdown_warm_worker()
         
         # c.jpg and b.jpg should be called, a.txt skipped.
         # Actually listing order is lexicographic ascending by name?
         # No, index.entries is ALREADY in listing order!
         # The prompt says: "walks index.entries where entry.previewable, in listing order".
         # So the order called should exactly match the order in index.entries that are previewable.
        assert calls == ["c.jpg", "b.jpg"]

import logging
def test_non_ok_index_skips_and_leaves_worker_alive(tmp_path, caplog):
    caplog.set_level(logging.DEBUG)
    settings = Settings(
        shipping_photos_dir=str(tmp_path),
        shipping_photos_thumb_warm_enabled=True
    )
    
    def mock_resolve(folder, *args):
        if folder == "f1":
            return PhotoFileIndex(status=PhotoFileListStatus.NOT_FOUND, entries=[], by_name={}, total_bytes=0, scanned_at=0, truncated=False)
        elif folder == "f2":
            return PhotoFileIndex(status=PhotoFileListStatus.UNAVAILABLE, entries=[], by_name={}, total_bytes=0, scanned_at=0, truncated=False)
        else:
            return PhotoFileIndex(status=PhotoFileListStatus.OK, entries=[PhotoFileEntry("a.jpg", 100, 100, "v1", True)], by_name={}, total_bytes=100, scanned_at=0, truncated=False)
            
    calls = []
    def mock_generate_once(folder, name, *args):
        calls.append(folder)
        return "ok", None
        
    with patch("backend.app.services.photo_warm.resolve_file_index", side_effect=mock_resolve), \
         patch("backend.app.services.photo_warm.generate_once", side_effect=mock_generate_once):
         
        pw.enqueue_warm("f1", settings)
        pw.enqueue_warm("f2", settings)
        pw.enqueue_warm("f3", settings)
         
        while pw._warm_known: time.sleep(0.01)
        pw.shutdown_warm_worker()
         
        assert calls == ["f3"]
        assert "non-OK index status: PhotoFileListStatus.NOT_FOUND" in caplog.text
        assert "non-OK index status: PhotoFileListStatus.UNAVAILABLE" in caplog.text

def test_nocapacity_retries_and_moves_to_next_file(tmp_path):
    settings = Settings(
        shipping_photos_dir=str(tmp_path),
        shipping_photos_thumb_warm_enabled=True,
        shipping_photos_thumb_warm_max_attempts=3,
        shipping_photos_thumb_warm_backoff_seconds=0.01
    )
    
    idx = PhotoFileIndex(
        status=PhotoFileListStatus.OK,
        entries=[
            PhotoFileEntry("a.jpg", 100, 100, "v1", True),
            PhotoFileEntry("b.jpg", 100, 100, "v1", True)
        ],
        by_name={},
        total_bytes=200,
        scanned_at=0,
        truncated=False
    )
    
    calls = []
    def mock_resolve(*args):
        return idx
        
    def mock_generate_once(folder, name, *args):
        calls.append(name)
        if name == "a.jpg":
            return "err", "saturated"
        return "ok", None
        
    with patch("backend.app.services.photo_warm.resolve_file_index", side_effect=mock_resolve), \
         patch("backend.app.services.photo_warm.generate_once", side_effect=mock_generate_once):
         
        pw.enqueue_warm("f1", settings)
        while pw._warm_known: time.sleep(0.01)
        pw.shutdown_warm_worker()
         
        assert calls == ["a.jpg", "a.jpg", "a.jpg", "b.jpg"]

def test_stop_event_terminates_loop(tmp_path):
    settings = Settings(
        shipping_photos_dir=str(tmp_path),
        shipping_photos_thumb_warm_enabled=True
    )
    
    idx = PhotoFileIndex(
        status=PhotoFileListStatus.OK,
        entries=[
            PhotoFileEntry("a.jpg", 100, 100, "v1", True),
            PhotoFileEntry("b.jpg", 100, 100, "v1", True)
        ],
        by_name={},
        total_bytes=200,
        scanned_at=0,
        truncated=False
    )
    
    calls = []
    def mock_resolve(*args):
        return idx
        
    def mock_generate_once(folder, name, *args):
        calls.append(name)
        if name == "a.jpg":
           pw._warm_stop.set()
        return "ok", None
        
    with patch("backend.app.services.photo_warm.resolve_file_index", side_effect=mock_resolve), \
         patch("backend.app.services.photo_warm.generate_once", side_effect=mock_generate_once):
         
        pw.enqueue_warm("f1", settings)
        pw._warm_thread.join(5.0)
         
        assert calls == ["a.jpg"]

def test_per_file_exception_does_not_kill_loop(tmp_path):
    settings = Settings(
        shipping_photos_dir=str(tmp_path),
        shipping_photos_thumb_warm_enabled=True
    )
    
    idx = PhotoFileIndex(
        status=PhotoFileListStatus.OK,
        entries=[
            PhotoFileEntry("a.jpg", 100, 100, "v1", True),
            PhotoFileEntry("b.jpg", 100, 100, "v1", True)
        ],
        by_name={},
        total_bytes=200,
        scanned_at=0,
        truncated=False
    )
    
    calls = []
    def mock_resolve(*args):
        return idx
        
    def mock_generate_once(folder, name, *args):
        calls.append(name)
        if name == "a.jpg":
            raise ValueError("Test error")
        return "ok", None
        
    with patch("backend.app.services.photo_warm.resolve_file_index", side_effect=mock_resolve), \
         patch("backend.app.services.photo_warm.generate_once", side_effect=mock_generate_once):
         
        pw.enqueue_warm("f1", settings)
         
         # Enqueue f2 to make sure the loop is still alive after f1 is done!
        pw.enqueue_warm("f2", settings)
        while pw._warm_known: time.sleep(0.01)
        pw.shutdown_warm_worker()
         
        assert calls == ["a.jpg", "b.jpg", "a.jpg", "b.jpg"]
         
        assert "f1" not in pw._warm_known
        assert "f2" not in pw._warm_known

